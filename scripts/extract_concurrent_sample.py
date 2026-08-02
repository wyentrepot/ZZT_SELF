# -*- coding: utf-8 -*-
"""从 303MB 原始报文流式提取含并发抄表帧（11 03 00 00）的行，生成受控小样本。

只扫描源文件一次，不读取完整文件进内存；用于端到端验收而非反复扫描大文件。
"""
import sys

SRC = r"D:\2-侦听台改造\测试文件\并发抄表-测试文件\原始报文自动保存 - 2026-06-30.txt"
DST = r"D:\2-侦听台改造\测试文件\并发抄表-样本.txt"
TARGET_MARK = " 11 03 00 00 "
LIMIT = 200  # 提取条数上限


def main():
    written = 0
    with open(SRC, "r", encoding="utf-8", errors="replace") as fin, \
            open(DST, "w", encoding="utf-8", newline="\n") as fout:
        for line in fin:
            if TARGET_MARK in line:
                fout.write(line)
                written += 1
                if written >= LIMIT:
                    break
    print(f"written={written}")


if __name__ == "__main__":
    sys.exit(main())
