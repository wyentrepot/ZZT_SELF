"""并发抄表滑窗调度器（REQS-0027 G5）。

口径（蒸馏库 CCO实现逻辑/01-事务框架 + 03_QGDW10376.2 §4.15）：
- 最大并发数可配（拒绝码 6D=超最大并发数，CCO aps_max_trans 上限）；
- 滑窗调度：每收到一个应答（成功/否认/超时均释放槽位）立即补发队列中
  下一块表，始终保持在途数 = 最大并发，直至队列发完；
- 每块表独立套 per-Fn 超时档位（单抄 59s / 并抄 99s）。

块帧模式：
- mode="single"：每表一帧 02H-F1（转发通信协议数据帧，嵌套 645 读数据）；
- mode="batch"：每表一帧 F1H-F1（集中器主动并发抄表，单表单帧滑动）。

应答匹配：轮询 io.rx_history_since(单调帧序号游标，只读语义；无游标 io
回退 len(rx_history()) 截断基线)，按
AFN/Fn + 帧内嵌表地址判定归属；表地址同时接受 CCO 档案线上字节（默认，
10H-F2 口径）与标准 645 BCD 反序/直序；否认帧（00H-F2）带否认码即判失败
并释放槽位。
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


_DEFAULT_ITEM = "02010100"  # 4 字节 DI：A 相电压（645-2007）


def _wire_addr_bytes(addr: str) -> bytes:
    """12 位地址串 → CCO 档案/devicelist 的 6 字节线上地址。

    CCO 10H-F2 档案地址按半字节交换展示（如 API 的 990042708102 对应线上
    99 00 24 07 18 20）；645 读帧必须使用同一线上字节，否则 CCO 查
    devicelist 失败会降级为广播，表侧不会应答。
    """
    raw = bytes.fromhex(addr)
    return bytes(((b & 0x0F) << 4) | ((b & 0xF0) >> 4) for b in raw)


def _masked_di(item: str) -> bytes:
    """4 字节 DI（8 位 hex）→ 645 数据域字节（DI0 在前，每字节 +0x33）。"""
    s = str(item or _DEFAULT_ITEM).strip()
    if len(s) != 8:
        raise ValueError(f"数据标识须为 8 位 hex（4 字节 DI）: {item!r}")
    try:
        di = bytes.fromhex(s)
    except ValueError as exc:
        raise ValueError(f"数据标识须为 8 位 hex（4 字节 DI）: {item!r}") from exc
    return bytes((b + 0x33) & 0xFF for b in di[::-1])


def meter_read_645(addr: str, item: str = _DEFAULT_ITEM,
                   addr_mode: str = "wire") -> bytes:
    """构造 645 读数据请求帧（控制码 0x11，读数据）。

    item: 4 字节 DI（8 位 hex），默认 A 相电压 02010100。
    addr_mode:
      - "wire"：CCO 档案/devicelist 的 6 字节线上地址（默认）
      - "bcd"：标准 645 BCD 低字节在前（旧行为，适用于 BCD 表地址）
    """
    a = str(addr).strip()
    if len(a) != 12 or not a.isdigit():
        raise ValueError(f"表地址须为 12 位数字: {addr!r}")
    mode = str(addr_mode or "wire").strip().lower()
    if mode == "wire":
        addr_bytes = _wire_addr_bytes(a)
    elif mode == "bcd":
        addr_bytes = bytes.fromhex(a)[::-1]
    else:
        raise ValueError(f"addr_mode 仅支持 wire/bcd: {addr_mode!r}")
    from parser_lib.adapters.adapter_645 import build_frame
    return build_frame(addr_bytes, 0x11, _masked_di(item))


def _addr_hexes(addr: str) -> tuple[str, ...]:
    """表地址在帧内的候选表示：wire / 645 BCD 反序 / 645 BCD 直序。"""
    a = str(addr).strip()
    if len(a) != 12 or not a.isdigit():
        return ()
    try:
        raw = bytes.fromhex(a)
    except ValueError:
        return ()
    wire = bytes(((b & 0x0F) << 4) | ((b & 0xF0) >> 4) for b in raw)
    return (wire.hex().upper(), raw[::-1].hex().upper(), raw.hex().upper())


def _addr_in_frame(raw: bytes, addr: str) -> bool:
    forms = _addr_hexes(addr)
    if not forms:
        return False
    h = raw.hex().upper()
    return any(f in h for f in forms)


def _decoded_seq(decoded: dict) -> Optional[int]:
    """信息域 R 最后一字节为报文序列号（无地址域本地帧）。"""
    raw = str(decoded.get("fields", {}).get("信息域R", {}).get("raw") or "")
    try:
        return int(raw[-2:], 16)
    except (TypeError, ValueError):
        return None


def _app_data(decoded: dict) -> bytes:
    """AFN/FN 之后、CS 之前的数据单元（仅无地址域本地帧：68+L2+C1+R6+AFN1+FN1）。"""
    try:
        raw = bytes.fromhex(str(decoded.get("raw_hex") or "").replace(" ", ""))
    except ValueError:
        return b""
    return raw[12:-2] if len(raw) >= 14 else b""


def _decoded_match_addr(decoded: dict, addr: str) -> bool:
    """按解析结构判定帧内是否出现该表地址。

    ① 嵌套 645/698 帧的地址域；② 链路层地址域 A（A1/A3）；③ 原始帧字节
    扣除信息域 R 后再搜（排除 seq/时间戳字节伪命中）。
    """
    forms = _addr_hexes(addr)
    if not forms:
        return False

    def hit(h: str) -> bool:
        return bool(h) and any(f in h.upper() for f in forms)

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
    return any(f in h for f in forms)


class BatchReadJob:
    """一次并发抄表任务：表队列 + 最大并发滑窗 + 明细行实时追加。"""

    def __init__(self, io, meters: List[str], *, max_concurrent: int = 5,
                 mode: str = "single", protocol_type: int = 2,
                 timeout: Optional[float] = None, item: str = _DEFAULT_ITEM,
                 addr_mode: str = "wire",
                 profile: Optional[dict] = None,
                 responder=None, job_id: Optional[str] = None,
                 seq_start: int = 0):
        self.id = job_id or f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.io = io
        self.meters = [str(m).strip() for m in meters if str(m).strip()]
        self.max_concurrent = max(1, int(max_concurrent))
        self.mode = mode if mode in ("single", "batch") else "single"
        self.protocol_type = int(protocol_type)
        self.item = str(item or _DEFAULT_ITEM).strip()
        self.addr_mode = str(addr_mode or "wire").strip().lower()
        if len(self.item) != 8:
            raise ValueError(f"数据标识须为 8 位 hex（4 字节 DI）: {item!r}")
        try:
            bytes.fromhex(self.item)
        except ValueError as exc:
            raise ValueError(f"数据标识须为 8 位 hex（4 字节 DI）: {item!r}") from exc
        if self.addr_mode not in ("wire", "bcd"):
            raise ValueError(f"addr_mode 仅支持 wire/bcd: {addr_mode!r}")
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
            "item": self.item,
            "addr_mode": self.addr_mode,
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
            "item": self.item,
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
            app_payload = meter_read_645(meter, self.item, self.addr_mode)
        except Exception as e:
            self._row(meter, "error", started=started, note=f"构帧失败: {e}")
            return
        try:
            from sim_concentrator.frame_codec import build_13762_frame
            afn = 0x02 if self.mode == "single" else 0xF1
            self._seq += 1
            seq_sent = self._seq
            # 数据单元 = 规约类型 + 保留 + 长度L(低字节在前, 02 卡
            # datalen=(buff[3]<<8)+buff[2]) + 电表协议报文
            proto = bytes([self.protocol_type, 0x00]) \
                + len(app_payload).to_bytes(2, "little") + app_payload
            raw = build_13762_frame(afn=afn, fn=1, appdata=proto,
                                    direction="down", info={"seq": seq_sent},
                                    address=self._address())
            # 基线游标取在发送前一刻：此后新入历史的帧才是本次下发的候选应答。
            # 游标用单调帧序号（REQS-0039 LR-4）：len() 游标在历史截断到 1000 条
            # 后恒等，hist[seen:] 永远为空 → 所有应答匹配不到 → 全部超时。
            use_seq_cursor = hasattr(self.io, "rx_history_since")
            last_seq = (self.io.rx_cursor() if use_seq_cursor
                        else len(self.io.rx_history()))
            self.io.send_frame(raw)
        except Exception as e:
            self._row(meter, "error", started=started, note=f"发送失败: {e}")
            return

        # 滑窗核心：窗口内轮询历史帧（不消费），命中本表应答/否认/超时即释放槽位
        try:
            deadline = time.time() + timeout
            while time.time() < deadline and not self.stop_flag.is_set():
                if use_seq_cursor:
                    new_frames, last_seq = self.io.rx_history_since(last_seq)
                else:
                    hist = self.io.rx_history()
                    new_frames = hist[last_seq:]
                    last_seq = len(hist)
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
                    if up_afn == (0x02 if self.mode == "single" else 0xF1) and up_fn == 1:
                        if _decoded_match_addr(decoded, meter):
                            self._row(meter, "success", started=started,
                                      afn=f"{up_afn:02X}H", fn=f"F{up_fn}",
                                      reply_hex=hexs)
                            return
                        # 无地址域应答：CCO 本地口按请求序号回带 seq，用序号归属本表
                        if _decoded_seq(decoded) == seq_sent:
                            empty_len = 4 if self.mode == "batch" else 3
                            if len(_app_data(decoded)) > empty_len:
                                self._row(meter, "success", started=started,
                                          afn=f"{up_afn:02X}H", fn=f"F{up_fn}",
                                          reply_hex=hexs)
                            else:
                                self._row(meter, "nodata", started=started,
                                          afn=f"{up_afn:02X}H", fn=f"F{up_fn}",
                                          reply_hex=hexs,
                                          note="应答空数据（从节点离线/无抄读数据）")
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
        # REQS-0020/0021：CCO 本地口不接受带地址域的下行帧（会被否认 0A），
        # 与 scenario_codec 默认一致，单抄/并发抄表一律不带地址域。
        return {}

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
