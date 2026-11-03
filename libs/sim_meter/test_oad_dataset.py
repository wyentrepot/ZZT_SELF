"""698 采集 OAD 数据集单元测试（REQS-0037）。

固定用例取自 20261103 台架实证（/d/sta/01-sta/20261103-003456/）：
- 请求帧：STA 分钟采集真实下行（sm-20261103-004404 会话抓帧），
  SECURITY-Request(明文) 包 GET-Request WithList（05 02 PIID 03 + 3 OAD + TimeTag 00）；
- 应答黄金帧：inject_698_reply.py 注入并让 STA 正常存数的同款帧
  （20000201→11 01、20010201→11 02、20000200 缺席）——脑按数据集应答必须
  与注入帧逐字节一致。
"""
import json

import pytest
from fastapi.testclient import TestClient

from parser_lib.adapters.adapter_698 import build_frame as build_698
from sim_meter import oad_dataset
from sim_meter.api import SimMeterService, create_simeter_app
from sim_meter.meter import MeterBrain
from sim_meter.protocol_698 import (
    FE_PREAMBLE,
    build_get_list_response,
    build_get_normal_response,
    parse_698_frame,
)

ADDR = bytes.fromhex("000000000001")     # 本表地址（6B）

# 20261103 台架真实抓帧：STA 分钟采集下行（6835…）
STA_CAPTURED_REQUEST = bytes.fromhex(
    "6835004305000000000001018B3810001105020103200002012001020120000200"
    "0001104C230EFE83644E9BA7C6FB6EC78837346F1C16")
# 同台架注入并让 STA 存数的应答黄金帧（inject_698_reply.py REPLY_HEX）
BENCH_GOLDEN_REPLY = bytes.fromhex(
    "FEFEFEFE682400C3050000000000010188D8900012850201"
    "0220000201011101200102010111022B0216")

# 台架任务 1 的 OAD 集合（F231 配置：三点、每点 2 字节）
BENCH_ENTRIES = [
    {"oad": "20000201", "data_hex": "1101", "note": "task1 点1"},
    {"oad": "20010201", "data_hex": "1102", "note": "task1 点2"},
    {"oad": "20000200", "missing": True, "length": 2, "note": "task1 点3 缺项"},
]


def _brain_no_file(monkeypatch, tmp_path) -> MeterBrain:
    """文件层为空（且不受本机 oad_dataset.json 影响）的脑。"""
    monkeypatch.setattr(oad_dataset, "default_dataset_path",
                        lambda: tmp_path / "no-oad-dataset.json")
    monkeypatch.delenv(oad_dataset.ENV_OAD_DATASET, raising=False)
    return MeterBrain()


# ---------------------------------------------------------------------------
# oad_dataset：校验 / 加载 / 路径解析
# ---------------------------------------------------------------------------
class TestOadDatasetNormalize:
    def test_valid_entry(self):
        e = oad_dataset.normalize_oad_entry(
            {"oad": "20000201", "data_hex": "11 01"})
        assert e["oad"] == "20000201" and e["data_hex"] == "1101"
        assert e["missing"] is False and e["length"] == 2

    def test_missing_entry(self):
        e = oad_dataset.normalize_oad_entry(
            {"oad": "20000200", "missing": True, "length": 2})
        assert e["missing"] is True and e["data_hex"] is None and e["length"] == 2

    def test_invalid_entries(self):
        with pytest.raises(ValueError, match="OAD 须为 8 位"):
            oad_dataset.normalize_oad_entry({"oad": "2000020", "data_hex": "11"})
        with pytest.raises(ValueError, match="互斥"):
            oad_dataset.normalize_oad_entry(
                {"oad": "20000201", "data_hex": "1101", "missing": True})
        with pytest.raises(ValueError, match="须配置 data_hex"):
            oad_dataset.normalize_oad_entry({"oad": "20000201"})
        with pytest.raises(ValueError, match="非十六进制"):
            oad_dataset.normalize_oad_entry(
                {"oad": "20000201", "data_hex": "XY"})
        with pytest.raises(ValueError, match="不符"):
            oad_dataset.normalize_oad_entry(
                {"oad": "20000201", "data_hex": "1101", "length": 4})

    def test_normalize_entries_dict_and_dup(self):
        out = oad_dataset.normalize_oad_entries({"oads": [
            {"oad": "20000201", "data_hex": "1101"},
            {"oad": "20 00 02 01", "data_hex": "2202"},      # 同 OAD 后者覆盖
        ]})
        assert out["20000201"]["data_hex"] == "2202"

    def test_load_file_shapes_and_errors(self, tmp_path):
        p = tmp_path / "ds.json"
        p.write_text(json.dumps({"oads": BENCH_ENTRIES}), encoding="utf-8")
        entries = oad_dataset.load_oad_dataset_file(p)
        assert [k for k in entries] == ["20000201", "20010201", "20000200"]
        assert entries["20000200"]["missing"] is True

        p2 = tmp_path / "list.json"
        p2.write_text(json.dumps([{"oad": "20000201", "data_hex": "1101"}]),
                      encoding="utf-8")
        assert list(oad_dataset.load_oad_dataset_file(p2)) == ["20000201"]

        p3 = tmp_path / "bad.json"
        p3.write_text("{oops", encoding="utf-8")
        with pytest.raises(ValueError, match="非合法 JSON"):
            oad_dataset.load_oad_dataset_file(p3)

    def test_load_file_invalid_entry_raises(self, tmp_path):
        p = tmp_path / "ds.json"
        p.write_text(json.dumps({"oads": [{"oad": "xx"}]}), encoding="utf-8")
        with pytest.raises(ValueError):
            oad_dataset.load_oad_dataset_file(p)


class TestOadDatasetResolve:
    def test_explicit_path_wins(self, tmp_path, monkeypatch):
        target = tmp_path / "explicit.json"
        monkeypatch.setenv(oad_dataset.ENV_OAD_DATASET, str(tmp_path / "env.json"))
        assert oad_dataset.resolve_dataset_path(str(target)) == target

    def test_env_path(self, tmp_path, monkeypatch):
        env_file = tmp_path / "env.json"
        monkeypatch.setenv(oad_dataset.ENV_OAD_DATASET, str(env_file))
        assert oad_dataset.resolve_dataset_path(None) == env_file

    def test_default_only_when_exists(self, tmp_path, monkeypatch):
        monkeypatch.delenv(oad_dataset.ENV_OAD_DATASET, raising=False)
        monkeypatch.setattr(oad_dataset, "default_dataset_path",
                            lambda: tmp_path / "none.json")
        assert oad_dataset.resolve_dataset_path(None) is None
        existing = tmp_path / "ds.json"
        existing.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(oad_dataset, "default_dataset_path",
                            lambda: existing)
        assert oad_dataset.resolve_dataset_path(None) == existing


# ---------------------------------------------------------------------------
# protocol_698：WithList 请求解析与两种应答构造
# ---------------------------------------------------------------------------
class TestParse698WithList:
    def test_sta_captured_request(self):
        p = parse_698_frame(STA_CAPTURED_REQUEST)
        assert p.control == 0x43
        assert p.security == 0x10                      # SECURITY-Request 明文包
        assert p.service == 0x05 and p.request_type == 2
        assert p.piid == 1 and p.list_num == 3
        assert [o.hex().upper() for o in p.oads] == \
            ["20000201", "20010201", "20000200"]
        assert p.oad is None                           # WithList 不填单 OAD

    def test_truncated_list_rejected(self):
        """L 域自洽但 OAD 数不足 → WithList 专项校验拒绝。"""
        inner = (bytes([0x05, 0x02, 0x01, 0x03])
                 + bytes.fromhex("20000201") + bytes.fromhex("20010201") + b"\x00")
        apdu = bytes([0x10, 0x00, len(inner)]) + inner
        req = FE_PREAMBLE + build_698(apdu, b"\x05" + ADDR, ca=0, control=0x43)
        with pytest.raises(ValueError, match="OAD 不完整"):
            parse_698_frame(req)


class TestBuildGetResponses:
    def test_list_response_plain_no_security_wrap(self):
        parsed = parse_698_frame(STA_CAPTURED_REQUEST)
        parsed.security = None                          # 强制明文路径
        wire = build_get_list_response(
            parsed, [(bytes.fromhex("20000201"), b"\x11\x01")])
        assert wire[:4] == FE_PREAMBLE
        p = parse_698_frame(wire)
        assert p.security is None and p.service == 0x85
        assert p.apdu == bytes([0x85, 0x02, 0x01, 0x01]) \
            + bytes.fromhex("20000201") + b"\x01\x11\x01"

    def test_normal_response(self):
        parsed = parse_698_frame(STA_CAPTURED_REQUEST)
        wire = build_get_normal_response(parsed, bytes.fromhex("20000201"),
                                         b"\x11\x01")
        p = parse_698_frame(wire)
        assert p.security == 0x90 and p.service == 0x85
        assert p.inner_apdu == (bytes([0x85, 0x01, 0x01])
                                + bytes.fromhex("20000201") + b"\x01\x11\x01")

    def test_bad_oad_rejected(self):
        parsed = parse_698_frame(STA_CAPTURED_REQUEST)
        with pytest.raises(ValueError, match="OAD 须为 4 字节"):
            build_get_list_response(parsed, [(b"\x20\x00", b"\x11")])


# ---------------------------------------------------------------------------
# MeterBrain：数据集匹配 / 缺项口径 / 未配置回退
# ---------------------------------------------------------------------------
class TestBrainOadDataset:
    def test_no_dataset_no_reply_unchanged(self, monkeypatch, tmp_path):
        """数据集为空时采集 OAD 不应答（与历史行为一致，不悄悄变）。"""
        brain = _brain_no_file(monkeypatch, tmp_path)
        res = brain.handle_frame(STA_CAPTURED_REQUEST)
        assert res.reply is None and res.reply_wire is None
        assert res.note["reason"] == "未知 OAD"
        assert "43000200" in res.note["func"]

    def test_bench_golden_frame_byte_identical(self, monkeypatch, tmp_path):
        """台架三 OAD 配置下，应答与 20261103 注入黄金帧逐字节一致。"""
        brain = _brain_no_file(monkeypatch, tmp_path)
        brain.set_oad_entries(entries=BENCH_ENTRIES)
        res = brain.handle_frame(STA_CAPTURED_REQUEST)
        assert res.reply_wire == BENCH_GOLDEN_REPLY
        assert res.note["func"] == "698 采集读点(WithList 2/3)"
        assert res.note["oads"] == ["20000201", "20010201"]
        assert res.note["oad_omitted"] == ["20000200(缺项)"]
        p = parse_698_frame(res.reply_wire)
        assert p.security == 0x90 and p.service == 0x85
        assert p.inner_apdu == (bytes([0x85, 0x02, 0x01, 0x02])
                                + bytes.fromhex("20000201") + b"\x01\x11\x01"
                                + bytes.fromhex("20010201") + b"\x01\x11\x02")

    def test_unconfigured_oad_in_list_omitted(self, monkeypatch, tmp_path):
        """部分 OAD 未配置：命中的照答，未配置的 omission（与缺项同线行为）。"""
        brain = _brain_no_file(monkeypatch, tmp_path)
        brain.set_oad_entries(entries=[
            {"oad": "20000201", "data_hex": "1101"}])
        res = brain.handle_frame(STA_CAPTURED_REQUEST)
        assert res.reply_wire is not None
        assert res.note["oads"] == ["20000201"]
        assert res.note["oad_omitted"] == ["20010201(未配置)", "20000200(未配置)"]
        p = parse_698_frame(res.reply_wire)
        assert p.inner_apdu[3] == 1                     # 只回 1 条

    def test_all_unconfigured_list_no_reply(self, monkeypatch, tmp_path):
        brain = _brain_no_file(monkeypatch, tmp_path)
        brain.set_oad_entries(entries=[
            {"oad": "30000201", "data_hex": "11"}])     # 配了别的 OAD
        res = brain.handle_frame(STA_CAPTURED_REQUEST)
        assert res.reply_wire is None
        assert res.note["reason"] == "未知 OAD"
        assert res.note["oad_omitted"] == ["20000201(未配置)",
                                           "20010201(未配置)",
                                           "20000200(未配置)"]

    def test_normal_request_answered_when_configured(self, monkeypatch, tmp_path):
        """Normal 单点读：命中的 OAD 也应答（85 01 PIID OAD 01 data）。"""
        brain = _brain_no_file(monkeypatch, tmp_path)
        brain.set_oad_entries(entries=[{"oad": "20000201", "data_hex": "1101"}])
        inner = bytes([0x05, 0x01, 0x01]) + bytes.fromhex("20000201") + b"\x00"
        apdu = bytes([0x10, 0x00, len(inner)]) + inner + b"\x01\x10" + b"\x00" * 16
        req = FE_PREAMBLE + build_698(apdu, b"\x05" + ADDR, ca=0, control=0x43)
        res = brain.handle_frame(req)
        assert res.reply_wire is not None
        assert res.note["func"] == "698 采集读点(20000201)"
        p = parse_698_frame(res.reply_wire)
        assert p.inner_apdu == (bytes([0x85, 0x01, 0x01])
                                + bytes.fromhex("20000201") + b"\x01\x11\x01")

    def test_normal_request_missing_configured_no_reply(self, monkeypatch, tmp_path):
        brain = _brain_no_file(monkeypatch, tmp_path)
        brain.set_oad_entries(entries=[{"oad": "20000201", "missing": True}])
        inner = bytes([0x05, 0x01, 0x01]) + bytes.fromhex("20000201") + b"\x00"
        apdu = bytes([0x10, 0x00, len(inner)]) + inner
        req = FE_PREAMBLE + build_698(apdu, b"\x05" + ADDR, ca=0, control=0x43)
        res = brain.handle_frame(req)
        assert res.reply_wire is None
        assert res.note["reason"] == "OAD 缺项"

    def test_runtime_overrides_file_and_reset(self, monkeypatch, tmp_path):
        """API（运行时层）优先级高于文件层；reset 回落文件层。"""
        file_ds = tmp_path / "ds.json"
        file_ds.write_text(json.dumps({"oads": [
            {"oad": "20000201", "data_hex": "2201"}]}), encoding="utf-8")
        brain = MeterBrain(oad_dataset_path=str(file_ds))
        res = brain.handle_frame(STA_CAPTURED_REQUEST)
        assert res.note["oads"] == ["20000201"]
        assert parse_698_frame(res.reply_wire).inner_apdu[-2:] == b"\x22\x01"

        brain.set_oad_entries(entries=[{"oad": "20000201", "data_hex": "3301"}])
        res = brain.handle_frame(STA_CAPTURED_REQUEST)
        assert parse_698_frame(res.reply_wire).inner_apdu[-2:] == b"\x33\x01"

        view = brain.set_oad_entries(reset=True)
        assert view["runtime"] == {} and view["effective"]["20000201"]["data_hex"] == "2201"

    def test_remove_runtime_entry(self, monkeypatch, tmp_path):
        brain = _brain_no_file(monkeypatch, tmp_path)
        brain.set_oad_entries(entries=[{"oad": "20000201", "data_hex": "1101"},
                                       {"oad": "20010201", "data_hex": "1102"}])
        view = brain.set_oad_entries(remove=["20000201"])
        assert "20000201" not in view["runtime"]
        assert "20010201" in view["runtime"]

    def test_set_entries_atomic_on_error(self, monkeypatch, tmp_path):
        """校验失败整体不生效（原有运行时条目保持不变）。"""
        brain = _brain_no_file(monkeypatch, tmp_path)
        brain.set_oad_entries(entries=[{"oad": "20000201", "data_hex": "1101"}])
        with pytest.raises(ValueError):
            brain.set_oad_entries(entries=[{"oad": "20010201", "data_hex": "zz"}])
        view = brain.get_oad_entries()
        assert list(view["runtime"]) == ["20000201"]

    def test_file_load_error_not_fatal(self, tmp_path):
        """坏文件不致命：file_error 可见，文件层为空，服务照常。"""
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        brain = MeterBrain(oad_dataset_path=str(bad))
        view = brain.get_oad_entries()
        assert view["file"] == {} and "非合法 JSON" in view["file_error"]
        assert view["file_path"] == str(bad)

    def test_missing_explicit_file_not_fatal(self, tmp_path):
        brain = MeterBrain(oad_dataset_path=str(tmp_path / "nope.json"))
        view = brain.get_oad_entries()
        assert view["file"] == {} and view["file_error"]

    def test_env_var_dataset(self, tmp_path, monkeypatch):
        ds = tmp_path / "env.json"
        ds.write_text(json.dumps({"oads": [
            {"oad": "20000201", "data_hex": "1101"}]}), encoding="utf-8")
        monkeypatch.setenv(oad_dataset.ENV_OAD_DATASET, str(ds))
        brain = MeterBrain()
        assert brain.get_oad_entries()["file_path"] == str(ds)


class TestExampleDatasetShipped:
    """随库示例文件：台架三 OAD 实测口径，加载即可复现黄金帧。"""

    def test_example_file_loads_bench_oads(self):
        entries = oad_dataset.load_oad_dataset_file(oad_dataset.example_dataset_path())
        assert entries["20000201"] == {
            "oad": "20000201", "missing": False, "data_hex": "1101",
            "length": 2, "note": "task1 点1：应答 2 字节 11 01（20261103 台架注入帧同款）"}
        assert entries["20010201"]["data_hex"] == "1102"
        assert entries["20000200"]["missing"] is True
        assert entries["20000200"]["length"] == 2

    def test_example_file_reproduces_golden_reply(self):
        brain = MeterBrain(oad_dataset_path=str(oad_dataset.example_dataset_path()))
        res = brain.handle_frame(STA_CAPTURED_REQUEST)
        assert res.reply_wire == BENCH_GOLDEN_REPLY


# ---------------------------------------------------------------------------
# REST API：GET/POST /api/simeter/oad
# ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(oad_dataset, "default_dataset_path",
                        lambda: tmp_path / "no-oad-dataset.json")
    monkeypatch.delenv(oad_dataset.ENV_OAD_DATASET, raising=False)
    svc = SimMeterService(brain=MeterBrain(), log_dir=None)
    app = create_simeter_app(service=svc)
    return TestClient(app)


class TestOadApi:
    def test_get_initial_view(self, client):
        r = client.get("/api/simeter/oad")
        assert r.status_code == 200
        body = r.json()
        assert body["file"] == {} and body["runtime"] == {}
        assert body["effective"] == {} and body["file_error"] is None

    def test_post_and_get_roundtrip(self, client):
        r = client.post("/api/simeter/oad", json={"entries": BENCH_ENTRIES})
        assert r.status_code == 200
        eff = r.json()["effective"]
        assert eff["20000201"] == {"oad": "20000201", "missing": False,
                                   "data_hex": "1101", "length": 2,
                                   "note": "task1 点1"}
        assert eff["20000200"]["missing"] is True
        r = client.get("/api/simeter/oad")
        assert list(r.json()["runtime"]) == ["20000201", "20010201", "20000200"]

    def test_handle_hex_golden_after_oad_set(self, client):
        """台架复现路径：API 配集 → handle_hex 喂真实抓帧 → 黄金应答。"""
        client.post("/api/simeter/oad", json={"entries": BENCH_ENTRIES})
        r = client.post("/api/simeter/handle_hex",
                        json={"hex": STA_CAPTURED_REQUEST.hex().upper()})
        assert r.status_code == 200
        body = r.json()
        assert body["note"]["func"] == "698 采集读点(WithList 2/3)"
        assert bytes.fromhex(body["reply_wire_hex"]) == BENCH_GOLDEN_REPLY

    def test_reset_falls_back_to_file_layer(self, client, tmp_path, monkeypatch):
        ds = tmp_path / "file-layer.json"
        ds.write_text(json.dumps({"oads": [
            {"oad": "20000201", "data_hex": "2201"}]}), encoding="utf-8")
        monkeypatch.setattr(oad_dataset, "default_dataset_path", lambda: ds)
        svc = SimMeterService(brain=MeterBrain(), log_dir=None)
        c = TestClient(create_simeter_app(service=svc))
        assert c.get("/api/simeter/oad").json()["effective"]["20000201"]["data_hex"] == "2201"
        c.post("/api/simeter/oad", json={"entries": [
            {"oad": "20000201", "data_hex": "3301"}]})
        assert c.get("/api/simeter/oad").json()["effective"]["20000201"]["data_hex"] == "3301"
        c.post("/api/simeter/oad", json={"reset": True})
        assert c.get("/api/simeter/oad").json()["effective"]["20000201"]["data_hex"] == "2201"

    def test_remove_endpoint(self, client):
        client.post("/api/simeter/oad", json={"entries": [
            {"oad": "20000201", "data_hex": "1101"}]})
        r = client.post("/api/simeter/oad", json={"remove": ["20000201"]})
        assert r.status_code == 200
        assert r.json()["runtime"] == {}

    def test_bad_entries_400(self, client):
        for bad in ({"entries": [{"oad": "2000020", "data_hex": "11"}]},
                    {"entries": [{"oad": "20000201"}]},
                    {"entries": [{"oad": "20000201", "data_hex": "1101",
                                  "missing": True}]},
                    {"entries": [{"oad": "20000201", "data_hex": "1101",
                                  "length": 4}]}):
            assert client.post("/api/simeter/oad", json=bad).status_code == 400
        assert client.get("/api/simeter/oad").json()["runtime"] == {}

    def test_meta_mentions_oad(self, client):
        meta = client.get("/api/simeter/_meta").json()
        assert "GET /oad" in meta["endpoints"] and "POST /oad" in meta["endpoints"]
        assert any("注入脚本" in n for n in meta["notes"])
        assert any("missing" in n for n in meta["notes"])
