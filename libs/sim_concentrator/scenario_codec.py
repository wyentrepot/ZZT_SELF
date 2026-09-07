"""用例语义化 → 13762 构帧转换层（ADR-5）。

把 task step 的 `send`（afn/fn + 最小业务参数）结合 profile（全局信息）
翻译成一次 build_13762_frame 调用：
- 地址域 A（REQS-0027）：**默认不带地址域**（module_id=0）。实机验证集中器
  不支持带地址域下行（否认 0A）；仅当显式指定 src/dst（params.dst、broadcast
  广播 A3=全 F）才装配地址域（module_id=1）。params.meters / params.addr 是业务
  数据单元（11H-F1 添加 / 11H-F2 删除对象），不参与地址域装配。
- 信息域 R：seq 由执行器自动分配递增（seq_auto=true 时）。
- 应用数据：调 parser_lib 的 encode_app_data 按 AFN/Fn 编码 params。

raw 整帧直发已彻底移除；未覆盖 Fn 由 encode_app_data 抛 UnsupportedFn。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from parser_lib.adapters.adapter_10376 import encode_app_data, UnsupportedFn
from sim_concentrator.frame_codec import build_13762_frame, fn_to_dt

# 广播地址 A3（6B 全 F）
_BROADCAST = "999999999999"


class ScenarioCodecError(ValueError):
    """用例描述 → 帧 转换失败（缺 profile、参数非法等）。"""


# ---------------------------------------------------------------------------
# profile 加载
# ---------------------------------------------------------------------------
def load_profile(profile: Optional[str],
                 profiles_dir: Optional[Path] = None) -> dict:
    """按 id 加载 profile JSON；未指定返回空 dict。

    profiles_dir 缺省为 <repo>/apps/workbench/scenarios/profiles。
    profile 仅作文件名（拒绝路径分隔符，防目录穿越）。
    """
    if not profile:
        return {}
    pid = str(profile)
    if any(ch in pid for ch in ("/", "\\", "..")) or pid in (".", ""):
        raise ScenarioCodecError(f"非法的 profile 名: {profile!r}")
    base = profiles_dir or _default_profiles_dir()
    p = base / f"{pid}.json"
    if not p.exists():
        raise ScenarioCodecError(f"profile 不存在: {profile}（查找 {p}）")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _default_profiles_dir() -> Path:
    # libs/sim_concentrator/scenario_codec.py -> 仓库根
    repo = Path(__file__).resolve().parent.parent.parent
    return repo / "apps" / "workbench" / "scenarios" / "profiles"


def resolve_arch_addr(meters, profile: dict):
    """把档案引用解析为 BCD 地址列表。

    支持：显式地址字符串 / {"addr": "..."} / {"ref": "sta1"}（查
    profile.sta_archives 的 id）。未知 ref 报错。
    """
    out = []
    archives = {a["id"]: a["addr"] for a in profile.get("sta_archives", [])}
    for m in meters:
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict):
            if "addr" in m:
                out.append(m["addr"])
            elif "ref" in m:
                ref = m["ref"]
                if ref not in archives:
                    raise ScenarioCodecError(f"未知档案引用 {ref!r}（profile 无此 id）")
                out.append(archives[ref])
            else:
                raise ScenarioCodecError(f"档案项缺 addr/ref: {m!r}")
        else:
            raise ScenarioCodecError(f"档案项非法: {m!r}")
    return out


# ---------------------------------------------------------------------------
# 地址域装配
# ---------------------------------------------------------------------------
def build_address(profile: dict, params: dict, direction: str,
                  explicit_src=None, explicit_dst=None) -> dict:
    """按方向装配地址域 {src, dst}（默认不带地址域，REQS-0027）。

    1376.2 实机验证（REQS-0020/0021）：CCO/集中器本地交互**不支持带地址域**的
    下行帧（带地址域查询被否认 0A）。故默认返回空 → module_id=0 无地址域。
    仅当显式指定 src/dst（如 params.dst/addr、meters、broadcast 广播 A3=全F）
    时才装配地址域（module_id=1）。

    下行：A1=cco_addr, A3=sta_addr（仅显式场景）。
    上行：A1=sta_addr, A3=cco_addr（应答回源，仍按显式 dst 装配）。
    广播：A3=999999999999H。
    """
    # REQS-0027：默认无地址域（module_id=0）。无显式目标 → 直接返回空。
    # 注意：params.meters / params.addr 是业务数据单元里的从节点地址
    # （11H-F1 添加 / 11H-F2 删除 / 档案管理），不是 1376.2 路由地址域 A，
    # **不**触发地址域装配。仅 params.dst / broadcast 视为显式路由目标。
    has_explicit_target = bool(
        explicit_src or explicit_dst
        or params.get("dst")
        or params.get("broadcast")
    )
    if not has_explicit_target:
        return {}

    cco = explicit_src or profile.get("cco_addr")
    if not cco:
        # 无 profile / 无 cco_addr：无地址域（module_id=0），等价旧 CCO 本地帧
        return {}

    if explicit_dst:
        dst = explicit_dst
    else:
        # 目标 sta 地址优先级：send 显式 dst > 广播
        dst = params.get("dst")
        if dst is None and params.get("broadcast"):
            dst = _BROADCAST

    if direction == "up":
        # 上行：CCO 是目的，sta 是源
        sta = dst or _BROADCAST
        return {"src": sta, "dst": cco}
    # 下行：A1=cco_addr；A3 优先级 = 显式 dst > params.addr/meters > 同址 cco
    return {"src": cco, "dst": dst or cco}


# ---------------------------------------------------------------------------
# send → 帧
# ---------------------------------------------------------------------------
def build_send(send: dict, profile: dict, seq: int = 1) -> bytes:
    """把 send 描述 + profile 构造成完整 1376.2 单 68 帧。

    send = {
      "afn": "11" | 0x11,
      "fn": "F231" | 231,
      "direction": "down" | "up"（缺省 down）,
      "params": {...},   # 该 AFN/Fn 的数据单元字段
      "comm_mode": 3,    # 可选，覆盖 profile
    }
    """
    if "afn" not in send or send.get("afn") is None:
        raise ScenarioCodecError("send 缺 afn")
    if "fn" not in send or send.get("fn") is None:
        raise ScenarioCodecError("send 缺 fn")
    if send.get("raw"):
        raise ScenarioCodecError(
            "send.raw 已移除（ADR-5 用例语义化），请改用 afn/fn + params")
    afn = _norm_afn(send.get("afn"))
    fn = _norm_fn(send.get("fn"))
    direction = send.get("direction", "down")
    params = dict(send.get("params") or {})
    comm_mode = int(send.get("comm_mode", profile.get("comm_mode", 3)))

    appdata = encode_app_data(afn, fn, params)  # 未覆盖 Fn 抛 UnsupportedFn

    seq_auto = profile.get("seq_auto", True)
    info = {"seq": seq if seq_auto else 0}

    address = build_address(profile, params, direction,
                            explicit_src=params.get("src"),
                            explicit_dst=params.get("dst"))

    return build_13762_frame(
        afn=afn, fn=fn, appdata=appdata,
        direction=direction, comm_mode=comm_mode,
        info=info, address=address,
    )


def _norm_afn(v) -> int:
    if isinstance(v, int):
        return v & 0xFF
    return int(str(v), 16)


def _norm_fn(v) -> int:
    """fn 支持 "F230" / "f230" / 230 / "230"。"""
    if isinstance(v, int):
        return v
    s = str(v).strip().upper()
    if s.startswith("F"):
        s = s[1:]
    return int(s, 10)
