from parser_lib.adapters.adapter_10376 import decode_frame_json
from parser_lib.protocol_13762 import decode
from sim_concentrator.frame_codec import build_13762_frame, frame_to_hex


def _request():
    nested = bytes.fromhex("6812345678901268910833333433AB896745CC16")
    raw = build_13762_frame(afn=0x02, fn=1, appdata=bytes([0x02, len(nested)]) + nested)
    return raw, {"frame": frame_to_hex(raw)}


def test_decode_is_strict_adapter_equality_oracle():
    raw, request = _request()
    for candidate in (request, {"frame_bytes": list(raw)}, {"frame": list(raw)}, None, []):
        assert decode(candidate) == decode_frame_json(candidate)


def test_decode_preserves_real_nested_645_shape():
    _, request = _request()
    expected = decode_frame_json(request)
    result = decode(request)
    assert result == expected
    assert result["ok"] is True
    assert result["nested"]
    assert result["nested"][0]["structure"] == "645"


def test_decode_invalid_input_matches_adapter_exactly():
    request = {"frame": "not hex"}
    assert decode(request) == decode_frame_json(request)
