# -*- coding: utf-8 -*-
"""DLL 与 Python 对并发抄表帧「转发数据长度/报文序号」解析一致性测试。

背景：dll/src/hplcFrame.cs 报文 ID=3 分支曾缺失 start+ 偏移，DLL 将
「转发数据长度/报文序号/选项字」读成 MAC 帧头偏移 2/4/6（如转发数据长度显示 12
而非实际 104）。修复后两侧应一致。
"""
import re
from pathlib import Path

from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.parser_service import ParserService

SAMPLE = Path("测试文件/并发抄表-样本.txt")
DLL_PATH = Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()


def _frame_at(index: int) -> str:
    lines = SAMPLE.read_text(encoding="utf-8", errors="replace").splitlines()
    line = lines[index]
    match = re.search(r"7E(?: [0-9A-Fa-f]{2})+ 7E$", line)
    assert match, f"样本第 {index} 行未匹配到完整帧"
    return match.group(0)


class TestDllPythonMeterFieldConsistency:
    def test_forward_len_and_seq_match_python_for_sample_frames(self):
        parser = ParserService(DotNetHplcParser(DLL_PATH))
        # 抽样 5 帧对比（含不同转发数据长度）
        for index in (0, 8, 20, 60, 120):
            result = parser.parse(_frame_at(index))
            full_load = result["full"]["MPDU"]["GW应用层"]["帧荷载解析"]
            py_fields = {
                f["name"]: f["raw"]
                for f in result["simple"]["application"]["fields"]
            }
            assert full_load["转发数据长度"] == py_fields["转发数据长度"], index
            assert full_load["报文序号"] == py_fields["报文序号"], index
            # DLL 选项字为 "0A"（无 0x 前缀），Python raw=10；按数值比较
            assert int(full_load["选项字"], 16) == py_fields["选项字"], index

    def test_forward_len_is_no_longer_mac_header_offset(self):
        """转发数据长度不再等于 MAC 帧头偏移 2 的 12 位值（原 bug 显示 12）。"""
        parser = ParserService(DotNetHplcParser(DLL_PATH))
        result = parser.parse(_frame_at(0))
        full_load = result["full"]["MPDU"]["GW应用层"]["帧荷载解析"]
        mac_hdr = result["full"]["MPDU"]["GW标准帧"]["原始数据"]
        mac_offset2_len = (
            (mac_hdr[3] << 4) | (mac_hdr[2] >> 4)
        )
        assert full_load["转发数据长度"] != mac_offset2_len
        assert full_load["转发数据长度"] > 0
