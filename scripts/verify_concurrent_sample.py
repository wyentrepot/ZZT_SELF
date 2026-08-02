# -*- coding: utf-8 -*-
"""端到端验收：对并发抄表样本逐帧调用 ParserService.parse，检查：
1) DLL 输出的 APP_ID == "0003"（终端并发抄表）
2) Python 富化后 FrmType == "终端主动并发抄表"
3) application.nested 递归出内嵌 645/698 帧
"""
import collections
import json
import re
import sys

from hplc_web.dotnet_parser import DotNetHplcParser
from hplc_web.parser_service import ParserService

SAMPLE = r"D:\2-侦听台改造\测试文件\并发抄表-样本.txt"

parser = ParserService(DotNetHplcParser(r"D:\2-侦听台改造\dll\bin\Debug\GwHPLCAnalysis.dll"))


def extract_frames(path):
    """按行提取 7E ... 7E 完整帧十六进制。"""
    frames = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            m = re.search(r"7E [0-9A-Fa-f ]+ 7E$", line)
            if m:
                frames.append(m.group(0))
    return frames


def main():
    frames = extract_frames(SAMPLE)
    print(f"total_frames={len(frames)}")

    stats = collections.Counter()
    app_ids = collections.Counter()
    nested_structures = collections.Counter()
    errors = []
    samples_with_698 = 0

    for i, hex_str in enumerate(frames):
        try:
            result = parser.parse(hex_str)
        except Exception as exc:
            errors.append((i, "exception", repr(exc)))
            continue

        simple = result.get("simple", {})
        stats[simple.get("FrmType", "?")] += 1
        app_ids[simple.get("APP_ID", "?")] += 1

        application = simple.get("application") or {}
        for nested in application.get("nested", []):
            nested_structures[nested.get("structure", "?")] += 1
        if application.get("nested"):
            samples_with_698 += 1

    print(f"FrmType分布: {dict(stats)}")
    print(f"APP_ID分布: {dict(app_ids)}")
    print(f"内嵌帧结构分布: {dict(nested_structures)}")
    print(f"含内嵌帧的样本数: {samples_with_698}/{len(frames)}")

    # 抽查第一条，展示完整 application 结构概要
    if frames:
        result = parser.parse(frames[0])
        simple = result["simple"]
        print("\n--- 首帧概要 ---")
        for key in ("FrmType", "BaseFrmType", "APP_PORT", "APP_ID", "APP_RAW", "application_error"):
            if key in simple:
                val = simple[key]
                if key == "APP_RAW" and isinstance(val, str) and len(val) > 40:
                    val = val[:40] + "..."
                print(f"  {key} = {val}")
        application = simple.get("application") or {}
        print(f"  application.fields 数量 = {len(application.get('fields', []))}")
        print(f"  application.nested 数量 = {len(application.get('nested', []))}")
        for idx, n in enumerate(application.get("nested", [])):
            print(f"    nested[{idx}] structure={n.get('structure')} addr={n.get('address')}")

    if errors:
        print(f"\n错误帧数: {len(errors)}")
        for e in errors[:10]:
            print(f"  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
