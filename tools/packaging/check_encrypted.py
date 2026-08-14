"""检测工作区 .py 是否处于 E-SafeNet 透明加密状态。

E-SafeNet 会把 .py 等扩展名在磁盘上以密文存放（文件头 0x62 0x14 = "b.#" 魔数，
随后的 0x45 0x2D 0x53 0x61 0x66 0x65 0x4E 0x65 0x74 = "E-SafeNet"）。
PyInstaller 直接读密文会报 SyntaxError，因此 build_exe.bat 据此决定
是否先 git archive HEAD 导出明文副本再构建。

用法：python check_encrypted.py <相对路径>
退出码：0 = 明文（可直接构建）；1 = 密文（需要 git 明文副本）；2 = 文件不存在
"""
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_encrypted.py <path>", file=sys.stderr)
        return 2
    p = Path(sys.argv[1])
    if not p.is_file():
        print(f"[check_encrypted] 文件不存在: {p}", file=sys.stderr)
        return 2
    head = p.read_bytes()[:2]
    # E-SafeNet 密文魔数 "b.#" = 0x62 0x14
    if head == b"\x62\x14":
        print("encrypted")
        return 1
    print("plain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
