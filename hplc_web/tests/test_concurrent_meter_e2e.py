# -*- coding: utf-8 -*-
"""并发抄表帧端到端验收测试（真实 DLL + 真实样本）。

样本来源：测试文件/并发抄表-测试文件/原始报文自动保存 - 2026-06-30.txt
（303MB 真实大日志），由 scripts/extract_concurrent_sample.py 流式提取前 200 条
含 `11 03 00 00` 的帧。

断言：
- 200 帧全部识别为 APP_ID == "0003"（终端主动并发抄表）
- Python 富化后 FrmType == "终端主动并发抄表"
- 每帧 application.nested 递归出内嵌 698.45 帧
- 适配器失败时保留 application_error（不阻塞建索引）
"""
import re
from pathlib import Path

from hplc_web.application_service import ApplicationAnalysisService
from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.parser_service import ParserService

SAMPLE = Path("测试文件/并发抄表-样本.txt")
DLL_PATH = Path("dll/bin/Debug/GwHPLCAnalysis.dll").resolve()


def _extract_frames(path: Path):
    frames = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.search(r"](7E(?: [0-9A-Fa-f]{2})+)", line)
        if match:
            frames.append(match.group(1))
    return frames


def _parse_all(parser: ParserService, frames):
    results = []
    for hex_str in frames:
        try:
            results.append(parser.parse(hex_str))
        except Exception as exc:  # 逐帧容错，便于统计
            results.append({"simple": {"FrmType": "EXCEPTION", "error": repr(exc)}})
    return results


class TestConcurrentMeterE2E:
    def test_sample_exists_and_has_200_frames(self):
        assert SAMPLE.exists()
        frames = _extract_frames(SAMPLE)
        assert len(frames) == 200, f"样本应含 200 帧，实际 {len(frames)}"

    def test_all_200_frames_are_concurrent_meter(self):
        parser = ParserService(DotNetHplcParser(DLL_PATH))
        frames = _extract_frames(SAMPLE)
        results = _parse_all(parser, frames)

        assert all(r["simple"].get("APP_ID") == "0003" for r in results)
        assert all(
            r["simple"].get("FrmType") == "终端主动并发抄表" for r in results
        )

    def test_all_frames_enrich_nested_698(self):
        parser = ParserService(DotNetHplcParser(DLL_PATH))
        frames = _extract_frames(SAMPLE)
        results = _parse_all(parser, frames)

        for r in results:
            simple = r["simple"]
            assert "application_error" not in simple, simple.get("application_error")
            application = simple.get("application")
            assert application is not None
            assert any(
                n.get("structure") == "698.45" for n in application.get("nested", [])
            )

    def test_application_service_routes_meter_frame_without_dll(self):
        """纯 Python 路径：0003 APP_RAW 富化（不依赖 DLL）。"""
        app_hex = (
            "110300000102630859050100688400c30535378109003010f18390006b850337"
            "5002020008002021020000200104000020000200002001020000200402000020"
            "0a02000000100201000020020101011c07ea061d0e1e00050000000001011208"
            "a30101050000000001020500000000050000000001021003e81003e806000000"
            "0006000000000000010004d0c1a502010016"
        )
        out = ApplicationAnalysisService().enrich_summary(
            {"FrmType": "终端主动并发抄表", "APP_PORT": "11", "APP_ID": "0003",
             "APP_RAW": app_hex}
        )
        assert out["FrmType"] == "终端主动并发抄表"
        assert out["BaseFrmType"] == "终端主动并发抄表"
        assert any(n["structure"] == "698.45" for n in out["application"]["nested"])
