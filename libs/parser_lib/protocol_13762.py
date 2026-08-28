from __future__ import annotations

from parser_lib.adapters.adapter_10376 import decode_frame_json


def decode(request: dict) -> dict:
    """Decode one 1376.2 request through the public parser adapter."""
    return decode_frame_json(request)
