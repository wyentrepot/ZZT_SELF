# -*- coding: utf-8 -*-
"""高频采集失败分析（tools/taiti/高频采集）。

按日志来源分目录：
    台体/   —— 台体日志分析（动作序列 / 失败表 / 组网成功率 / 最终判定）
    CCO/    —— CCO 日志二次证据（ApsReadRecord / aps tx）
    侦听台/ —— 侦听台 HPLC 报文二次证据（请求/应答帧）

用法（在 tools/taiti/高频采集 目录下）：
    python -m 高频采集 taish <台体日志>
    python -m 高频采集 cco <CCO日志> <表地址>... [--start .. --end ..]
    python -m 高频采集 sniff <侦听台报文> <表地址>... [--start .. --end ..]
    python -m 高频采集 cross <CCO日志> <侦听台报文> <表地址>... [--start .. --end ..]

复用项目 libs/parser_lib 与 libs/sim_concentrator.frame_codec。
"""
from __future__ import annotations

__version__ = "2.0.0"
