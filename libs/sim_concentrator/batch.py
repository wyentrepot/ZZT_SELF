"""并发抄表滑窗调度器（REQS-0027 G5）。

口径（蒸馏库 CCO实现逻辑/01-事务框架 + 03_QGDW10376.2 §4.15）：
- 最大并发数可配（拒绝码 6D=超最大并发数，CCO aps_max_trans 上限）；
- 滑窗调度：每收到一个应答（成功/否认/超时均释放槽位）立即补发队列中
  下一块表，始终保持在途数 = 最大并发，直至队列发完；
- 每块表独立套 per-Fn 超时档位（单抄 59s / 并抄 99s）。

块帧模式：
- mode="single"：每表一帧 02H-F1（转发通信协议数据帧，嵌套 645 读数据）；
- mode="batch"：每表一帧 F1H-F1（集中器主动并发抄表，单表单帧滑动）。

应答匹配：轮询 io.rx_history()（只读语义，与 expect_history 对齐），按
AFN/Fn + 帧内嵌表地址（645 地址 BCD 反序）判定归属；否认帧（00H-F2）带
否认码即判失败并释放槽位。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sim_concentrator import expect_rules
from sim_concentrator.frame_codec import decode_frame, frame_to_hex
from sim_concentrator.matcher import deny_info


def meter_read_645(addr: str) -> bytes:
    """构造 645 读数据请求帧（DI=00000000H，即报文中的 33 33 33 33）。

    645 地址域低字节在前（BCD 反序）；数据域经 +0x33 掩码后 DI 全 0 → 33H×4。
    """
    a = str(addr).strip()
    if len(a) != 12 or not a.isdigit():
        raise ValueError(f"表地址须为 12 位数字: {addr!r}")
    addr_bytes = bytes.fromhex(a)[::-1]
    from parser_lib.adapters.adapter_645 import build_frame
    return build_frame(addr_bytes, 0x91, bytes([0x33] * 4))


def _addr_hexes(addr: str) -> tuple[str, str]:
    """表地址的两种帧内表示：645 地址域 BCD 反序（le）/ 1376.2 直序（be）。"""
    a = str(addr).strip()
    if len(a) != 12 or not a.isdigit():
        return "", ""
    try:
        return bytes.fromhex(a)[::-1].hex().upper(), bytes.fromhex(a).hex().upper()
    except ValueError:
        return "", ""


def _addr_in_frame(raw: bytes, addr: str) -> bool:
    le, be = _addr_hexes(addr)
    if not le:
        return False
    h = raw.hex().upper()
    return le in h or be in h


def _decoded_match_addr(decoded: dict, addr: str) -> bool:
    """按解析结构判定帧内是否出现该表地址。

    ① 嵌套 645/698 帧的地址域；② 链路层地址域 A（A1/A3）；③ 原始帧字节
    扣除信息域 R 后再搜（排除 seq/时间戳字节伪命中）。
    """
    le, be = _addr_hexes(addr)
    if not le:
        return False

    def hit(h: str) -> bool:
        return bool(h) and (le in h.upper() or be in h.upper())

    for n in decoded.get("nested", []):
        f = n.get("fields", {}).get("地址域A") or {}
        if hit(str(f.get("hex") or f.get("value") or "")):
            return True
        for it in n.get("items", []):
            if hit(str(it.get("hex") or "")):
                return True
    fields = decoded.get("fields", {})
    a_field = fields.get("地址域A", {})
    if hit(str(a_field.get("hex") or a_field.get("value") or "")):
        return True
    h = str(decoded.get("raw_hex") or "").upper()
    info = str(fields.get("信息域R", {}).get("hex") or "").upper()
    if info and info in h:
        h = h.replace(info, "", 1)
    return le in h or be in h


class BatchReadJob:
    """一次并发抄表任务：表队列 + 最大并发滑窗 + 明细行实时追加。"""

    def __init__(self, io, meters: List[str], *, max_concurrent: int = 5,
                 mode: str = "single", protocol_type: int = 2,
                 timeout: Optional[float] = None, profile: Optional[dict] = None,
                 responder=None, job_id: Optional[str] = None,
                 seq_start: int = 0):
        self.id = job_id or f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.io = io
        self.meters = [str(m).strip() for m in meters if str(m).strip()]
        self.max_concurrent = max(1, int(max_concurrent))
        self.mode = mode if mode in ("single", "batch") else "single"
        self.protocol_type = int(protocol_type)
        self.profile = profile or {}
        self.responder = responder
        self._seq = int(seq_start)
        if timeout is not None:
            self.timeout_meta = {"seconds": float(timeout), "tier": "manual", "note": "页面手动覆盖"}
        else:
            self.timeout_meta = expect_rules.timeout_for(
                0x02 if self.mode == "single" else 0xF1, 1)

        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.thread: Optional[threading.Thread] = None
        afn = 0x02 if self.mode == "single" else 0xF1
        self.state: Dict[str, Any] = {
            "job_id": self.id,
            "mode": self.mode,
            "afn_fn": f"{afn:02X}H-F1",
            "meters_total": len(self.meters),
            "max_concurrent": self.max_concurrent,
            "timeout": self.timeout_meta,
            "in_flight": 0,
            "queued": len(self.meters),
            "done": 0,
            "success": 0,
            "failed": 0,
            "deny_breakdown": {},
            "finished": False,
            "error": None,
            "rows": [],
        }

    # -- 状态 -------------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            s = {k: v for k, v in self.state.items() if not k.startswith("_")}
            s["rows"] = list(self.state["rows"])
            return s

    # -- 行记录 ------------------------------------------------------------
    def _row(self, meter: str, status: str, *, started: float,
             afn: Optional[str] = None, fn: Optional[str] = None,
             deny: Optional[dict] = None, reply_hex: str = "",
             note: str = "") -> dict:
        row = {
            "meter": meter,
            "afn_fn": (f"{afn}-{fn}" if afn else self.state["afn_fn"]),
            "ts": datetime.now().isoformat(timespec="seconds"),
            "elapsed_ms": int((time.time() - started) * 1000),
            "status": status,               # success / deny / timeout / error
            "deny_code": (deny or {}).get("code_hex"),
            "deny_text": (deny or {}).get("text"),
            "reply_hex": reply_hex[-96:],
            "note": note,
        }
        with self.lock:
            self.state["rows"].append(row)
            self.state["done"] += 1
            self.state["in_flight"] = max(0, self.state["in_flight"] - 1)
            if status == "success":
                self.state["success"] += 1
            else:
                self.state["failed"] += 1
                key = deny["code_hex"] if deny else status
                self.state["deny_breakdown"][key] = self.state["deny_breakdown"].get(key, 0) + 1
        return row

    # -- 单块执行 ----------------------------------------------------------
    def _read_one(self, meter: str) -> None:
        """下发一块表的抄读帧并等待应答/超时（占用一个槽位直到释放）。"""
        started = time.time()
        timeout = self.timeout_meta["seconds"]
        try:
            app_payload = meter_read_645(meter)
        except Exception as e:
            self._row(meter, "error", started=started, note=f"构帧失败: {e}")
            return
        try:
            from sim_concentrator.frame_codec import build_13762_frame
            afn = 0x02 if self.mode == "single" else 0xF1
            self._seq += 1
            proto = bytes([self.protocol_type, 0x00]) \
                + len(app_payload).to_bytes(2, "big") + app_payload
            raw = build_13762_frame(afn=afn, fn=1, appdata=proto,
                                    direction="down", info={"seq": self._seq},
                                    address=self._address())
            # 基线游标取在发送前一刻：此后新入历史的帧才是本次下发的候选应答
            seen = len(self.io.rx_history())
            self.io.send_frame(raw)
        except Exception as e:
            self._row(meter, "error", started=started, note=f"发送失败: {e}")
            return

        # 滑窗核心：窗口内轮询历史帧（不消费），命中本表应答/否认/超时即释放槽位
        try:
            deadline = time.time() + timeout
            while time.time() < deadline and not self.stop_flag.is_set():
                hist = self.io.rx_history()
                new_frames = hist[seen:]
                seen = len(hist)
                for hf in new_frames:
                    try:
                        decoded = decode_frame(hf)
                    except Exception:
                        continue
                    up_afn = decoded.get("fields", {}).get("AFN", {}).get("raw")
                    up_fn = decoded.get("fields", {}).get("FN", {}).get("raw")
                    if (decoded.get("fields", {}).get("控制域C", {}).get("raw", 0) >> 7) & 1 != 1:
                        continue  # 只要上行帧
                    hexs = frame_to_hex(hf)
                    if up_afn == 0x00 and up_fn == 2:
                        d = deny_info(decoded)
                        if d is None:
                            continue
                        # 否认帧归属：A1/地址域含本表地址 → 直接归属；含其他表
                        # 地址 → 跳过；无地址信息 → 认领去重（同一帧只归属一个槽位）
                        if _decoded_match_addr(decoded, meter):
                            self._row(meter, "deny", started=started, afn="00H", fn="F2",
                                      deny=d, reply_hex=hexs)
                            return
                        if any(_decoded_match_addr(decoded, m)
                               for m in self.meters if m != meter):
                            continue
                        if self._claim_deny(hexs):
                            self._row(meter, "deny", started=started, afn="00H", fn="F2",
                                      deny=d, reply_hex=hexs)
                            return
                        continue
                    if up_afn == (0x02 if self.mode == "single" else 0xF1) and up_fn == 1 \
                            and _decoded_match_addr(decoded, meter):
                        self._row(meter, "success", started=started,
                                  afn=f"{up_afn:02X}H", fn=f"F{up_fn}",
                                  reply_hex=hexs)
                        return
                time.sleep(0.05)
            if not self.stop_flag.is_set():
                self._row(meter, "timeout", started=started,
                          note=f"超时({timeout}s)未收到期望帧")
        except Exception as e:
            self._row(meter, "error", started=started, note=f"等待异常: {e!r}")

    def _claim_deny(self, reply_hex: str) -> bool:
        """否认帧认领去重：并发槽位间同一否认帧只归属一个表。"""
        with self.lock:
            claimed = self.state.setdefault("_claimed_denies", set())
            if reply_hex in claimed:
                return False
            claimed.add(reply_hex)
            return True

    def _address(self) -> dict:
        cco = self.profile.get("cco_addr")
        if not cco:
            return {}
        return {"src": cco, "dst": cco}

    # -- 主循环（滑窗）-------------------------------------------------------
    def run(self) -> None:
        queue = list(self.meters)
        workers: List[threading.Thread] = []
        try:
            while queue and not self.stop_flag.is_set():
                # 补位：保持在途数 = max_concurrent
                slots = self.max_concurrent - self.state["in_flight"]
                for _ in range(max(0, slots)):
                    if not queue or self.stop_flag.is_set():
                        break
                    meter = queue.pop(0)
                    with self.lock:
                        self.state["queued"] = len(queue)
                        self.state["in_flight"] += 1
                    t = threading.Thread(target=self._read_one, args=(meter,),
                                         name=f"batch-{self.id}-{meter}", daemon=True)
                    t.start()
                    workers.append(t)
                time.sleep(0.05)
            for t in workers:
                t.join()
        except Exception as e:  # pragma: no cover
            with self.lock:
                self.state["error"] = repr(e)
        finally:
            with self.lock:
                self.state["finished"] = True
                self.state["queued"] = 0

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name=f"batch-{self.id}",
                                       daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_flag.set()
