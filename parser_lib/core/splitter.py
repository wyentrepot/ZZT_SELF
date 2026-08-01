"""帧切分器：处理粘包 / 半包 / 跨块缓冲。

设计：协议无关。切帧委托给各适配器的 try_extract（适配器最懂自己的帧边界）。
循环尝试已注册适配器，第一个通过结构校验的即切出一帧；全部失败则停止，
残留字节即为半包/未知数据，等待后续 feed 续接。

M6 大文件加固（2026-07-10，79MB 压测暴露）：
1. **丢弃前导噪声**：大多数受支持协议帧首字节为 0x68；双模 4-3
   通用报文以 0x11/0x12/0x1A 作为端口号起始。因此缓冲区首字节不在
   这些候选帧头内时，前导字节可安全丢弃。这让切帧器在日志文本 / 抓包噪声中
   持续前进，杜绝 pending 无界增长导致的内存暴涨。
2. **跳过伪帧头**：若缓冲区以 0x68 开头但长度已超过任何合法帧上限（MAX_FRAME）
   仍无法切出，则该 0x68 必是伪帧头（真帧不可能这么长），跳过 1 字节重新对齐。
   由此 pending 上界被约束在 MAX_FRAME 内，避免 O(n²) 全量重扫。
"""
from typing import Any, Dict, List

from .adapter import ProtocolAdapter, ExtractResult

# 任何合法帧的字节上限：698.45 L≤2000（约 2003B）、1376.2 同量级、645 ≤212B。
# 取 4096 作安全上界：缓冲区以 0x68 开头且超过此长度仍切不出 → 该 0x68 为伪帧头。
MAX_FRAME = 4096
FRAME_START_BYTES = (0x68, 0x11, 0x12, 0x1A)


class FrameSplitter:
    def __init__(self, adapters: List[ProtocolAdapter]):
        self.adapters = list(adapters)
        self._buf = bytearray()

    def _resync(self) -> bool:
        """把缓冲区对齐到下一个可能的帧头。返回是否发生了丢弃/前进。"""
        buf = self._buf
        if not buf:
            return False
        if buf[0] not in FRAME_START_BYTES:
            # 丢弃前导非帧噪声：定位下一个可能帧头
            positions = [p for p in (buf.find(b) for b in FRAME_START_BYTES) if p >= 0]
            if not positions:
                buf.clear()               # 全是噪声 → 清空
            else:
                nxt = min(positions)
                del buf[:nxt]             # 丢到下一个 0x68
            return True
        # 首字节是 0x68 但切不出：仅当长度已超过合法帧上限，判定为伪帧头，跳过 1 字节
        if buf[0] in (0x11, 0x12, 0x1A) and len(buf) >= 4:
            positions = [p for p in (buf.find(b, 1) for b in FRAME_START_BYTES) if p >= 0]
            if positions:
                del buf[:min(positions)]
                return True
        # Unsupported outer link envelope: its length includes both 68/16.
        # Skip a complete envelope so it cannot block subsequent known frames.
        if buf[0] == 0x68 and len(buf) >= 3:
            total = buf[1] | (buf[2] << 8)
            if 12 <= total <= MAX_FRAME and len(buf) >= total and buf[total - 1] == 0x16:
                del buf[:total]
                return True
            # DL/T 698 uses L+2 total bytes.  If that complete boundary has
            # already passed without a 0x16 terminator, this capture is
            # truncated/corrupt; move to the next plausible frame instead of
            # holding every later packet in pending forever.
            standard_total = total + 2
            if 12 <= total <= 2000 and len(buf) >= standard_total and buf[standard_total - 1] != 0x16:
                positions = [p for p in (buf.find(b, 1) for b in FRAME_START_BYTES) if p >= 0]
                if positions:
                    del buf[:min(positions)]
                    return True
        if len(buf) > MAX_FRAME:
            positions = [p for p in (buf.find(b, 1) for b in FRAME_START_BYTES) if p >= 0]
            if not positions:
                buf.clear()
            else:
                nxt = min(positions)
                del buf[:nxt]
            return True
        return False                       # 可能是尚未收全的半包 → 保留等待续接

    def feed(self, chunk: bytes) -> List[Dict[str, Any]]:
        """喂入一个字节块，返回本次切出的完整帧列表。"""
        self._buf.extend(chunk)
        out: List[Dict[str, Any]] = []
        while True:
            hit = None
            for adp in self.adapters:
                res = adp.try_extract(bytes(self._buf))
                if res is not None:
                    hit = (adp, res)
                    break
            if hit is not None:
                adp, res = hit
                out.append({"raw": res.raw, "complete": True, "protocol": adp.protocol})
                del self._buf[:res.consumed]
                continue
            # 无适配器切出 → 尝试重新对齐帧头；对齐后重试，无可对齐则停（半包/等续接）
            if not self._resync():
                break
        return out

    def pending(self) -> bytes:
        """返回尚未切出的残留字节（半包 / 未知数据）。"""
        return bytes(self._buf)

    def reset(self):
        self._buf = bytearray()
