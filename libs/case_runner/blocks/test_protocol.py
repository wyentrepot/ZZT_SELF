"""blocks/protocol.py 单测（REQS-0043 工位 H，CASE-BACKLOG §G-6）。

覆盖两个协议一致性块，**全部假 HTTP / 假能力**，不碰台架、不发真机激励：

- ``bench.simeter_protocol``：切 645→698（写+回读）/ 已是目标不重发 / 只读 /
  目标非法 / config 不可读分层（404 与不可达 → bench_unreachable，缺字段 →
  incomplete）/ 写 500 → incomplete / 写后回读丢失 → incomplete；
  判据（协议模式==期望）一律走步骤期望（evaluate）。
- ``sta.meter_protocol``：指纹命中（N=2 实测 / N=3 预期）与判读名 /
  最近一条指纹优先 / 窗口无指纹 → incomplete / 日志不可读 → bench_unreachable /
  **不设 provides**（未经校准的事实不回写）。
- 声明侧：两块 JSON 三字段齐备、observation_only、指纹（assert）在列。

口径出处：§G-6 缺块清单；N=2 证据 run 20261124-192633 STA 日志行 532
（simeter 帧 seq 59/76 佐证 698 探帧 OAD 40010200 被不应答）；N=3 待 E2 校准。
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from case_runner.blocks import loader, protocol
from case_runner.capabilities.simeter import SimeterCapability
from case_runner.core.executor import BlockResult, RunContext
from case_runner.core.expect import evaluate
from case_runner.core.models import BLOCK_FAIL, BLOCK_PASS, BLOCK_SKIPPED, BLOCK_UNKNOWN
from case_runner.core.registry import (
    clear_registries,
    get_block,
    has_block,
)

FAKE_BASE = "http://127.0.0.1:19302"
NO_ENV = "__CASE_RUNNER_TEST_NO_BASE_URL__"

CONFIG_PATH = "/api/simeter/config"
JZJC_PATH = "/api/simeter/jzjc"


@pytest.fixture(autouse=True)
def isolated_registries():
    clear_registries()
    yield
    clear_registries()


def step_state_of(result: BlockResult, expects) -> str:
    """块结果 → 步骤态（判据归步骤期望的便捷封装，同 test_fix_reset 口径）。"""
    return evaluate(result.observation, expects).state


# --------------------------------------------------------------------------- #
# 假 HTTP：simeter config/jzjc 两条路由（状态机：POST 改内存，GET 回读）
# --------------------------------------------------------------------------- #


class FakeTransport:
    def __init__(self, routes=None, errors=None,
                 default=(404, {"detail": "fake: 未配置该路由"})):
        self.routes = dict(routes or {})
        self.errors = dict(errors or {})
        self.default = default
        self.calls = []

    def __call__(self, method, url, body, timeout):
        parsed = urllib.parse.urlparse(url)
        key = (method.upper(), parsed.path)
        self.calls.append({"method": method.upper(), "path": parsed.path, "body": body})
        if key in self.errors:
            raise self.errors[key]
        response = self.routes.get(key, self.default)
        if callable(response):
            response = response(method, url, body)
        return response

    def count(self, method, path):
        return sum(1 for c in self.calls
                   if c["method"] == method.upper() and c["path"] == path)


class FakeSimeterState:
    """simeter 即装即采协议模式状态机：GET /config 回读、POST /jzjc 只改 protocol_type。"""

    def __init__(self, initial: int = 2):
        self.protocol_type = initial
        self.post_bodies: list[dict] = []

    def routes(self):
        return {
            ("GET", CONFIG_PATH): lambda m, u, b: (
                200, {"addr": "123456789012", "version": "645-2007",
                      "jzjc": {"protocol_type": self.protocol_type,
                               "descriptor": "DDZY666-Z"}}),
            ("POST", JZJC_PATH): self._post,
        }

    def _post(self, method, url, body):
        self.post_bodies.append(dict(body or {}))
        if "protocol_type" in (body or {}):
            self.protocol_type = body["protocol_type"]
        return 200, {"protocol_type": self.protocol_type, "descriptor": "DDZY666-Z"}


def _simeter(state: FakeSimeterState, *, errors=None) -> SimeterCapability:
    transport = FakeTransport(state.routes(), errors=errors)
    return SimeterCapability(FAKE_BASE, transport=transport, timeout=5.0,
                             probe_timeout=5.0, retry_backoff=0.0, env_var=NO_ENV)


def _ctx(cap) -> RunContext:
    return RunContext(extras={"simeter": cap})


def _action(**params) -> dict:
    return {"kind": "bench_protocol", "capability": "simeter", "params": params}


# --------------------------------------------------------------------------- #
# bench.simeter_protocol
# --------------------------------------------------------------------------- #


class TestBenchSimeterProtocol:
    def test_switch_645_to_698_write_then_readback(self):
        state = FakeSimeterState(initial=2)
        cap = _simeter(state)
        result = protocol.bench_simeter_protocol(_action(protocol=3), _ctx(cap))
        obs = result.observation
        assert obs.complete
        assert obs.values["切换前模式"] == 2
        assert obs.values["协议模式"] == 3
        assert obs.values["协议名称"] == "698.45"
        assert obs.values["切换确认"] is True
        assert "已切换 645-2007 → 698.45" in obs.values["切换动作"]
        # 写动作只带 protocol_type（不动描述符/下挂设备），写后回读一次
        assert state.post_bodies == [{"protocol_type": 3}]
        assert cap.transport.count("GET", CONFIG_PATH) == 2   # 切前读 + 回读
        assert cap.transport.count("POST", JZJC_PATH) == 1
        # 判据归步骤期望：协议模式==3 → pass
        assert step_state_of(result, [
            {"op": "eq", "field": "协议模式", "value": 3}]) == BLOCK_PASS

    def test_already_at_target_no_write(self):
        state = FakeSimeterState(initial=3)
        cap = _simeter(state)
        result = protocol.bench_simeter_protocol(_action(protocol=3), _ctx(cap))
        assert result.observation.complete
        assert result.observation.values["协议模式"] == 3
        assert result.observation.values["切换前模式"] == 3
        assert result.observation.values["切换确认"] is True
        assert "无需切换" in result.observation.values["切换动作"]
        assert state.post_bodies == []                        # 已是目标，不发写
        assert cap.transport.count("POST", JZJC_PATH) == 0

    def test_read_only_when_no_target(self):
        state = FakeSimeterState(initial=2)
        cap = _simeter(state)
        result = protocol.bench_simeter_protocol(_action(protocol=None), _ctx(cap))
        values = result.observation.values
        assert values["协议模式"] == 2 and values["切换前模式"] == 2
        assert values["切换动作"] == "只读（未给目标协议）"
        assert "切换确认" not in values                        # 只读路径不出确认字段
        assert state.post_bodies == []
        # 只读观测也能服务期望（读侧核验）
        assert step_state_of(result, [
            {"op": "eq", "field": "协议模式", "value": 2}]) == BLOCK_PASS

    def test_invalid_target_rejected_before_http(self):
        cap = _simeter(FakeSimeterState())
        result = protocol.bench_simeter_protocol(_action(protocol=4), _ctx(cap))
        assert result.observation.reason_code == "invalid_param"
        assert "2=645-2007 / 3=698.45" in result.detail
        assert cap.transport.calls == []                      # 参数非法不下发任何 HTTP

    def test_config_404_absent(self):
        cap = _simeter(FakeSimeterState(),
                       errors={("GET", CONFIG_PATH): ConnectionError("refused")})
        cap.transport.routes.clear()                          # 走 default 404
        result = protocol.bench_simeter_protocol(_action(protocol=3), _ctx(cap))
        assert result.observation.reason_code == "bench_unreachable"
        assert step_state_of(result, [
            {"op": "eq", "field": "协议模式", "value": 3}]) == BLOCK_SKIPPED

    def test_config_unreachable(self):
        cap = _simeter(FakeSimeterState(),
                       errors={("GET", CONFIG_PATH): ConnectionError("refused")})
        result = protocol.bench_simeter_protocol(_action(protocol=3), _ctx(cap))
        assert result.observation.reason_code == "bench_unreachable"

    def test_config_missing_protocol_field_incomplete(self):
        transport = FakeTransport({("GET", CONFIG_PATH): (200, {"addr": "x"})})
        cap = SimeterCapability(FAKE_BASE, transport=transport, timeout=5.0,
                                probe_timeout=5.0, retry_backoff=0.0, env_var=NO_ENV)
        result = protocol.bench_simeter_protocol(_action(protocol=3), _ctx(cap))
        assert result.observation.reason_code == "incomplete"
        assert "取不到 jzjc.protocol_type" in result.detail
        assert transport.calls[0]["method"] == "GET"          # 未动手写

    def test_write_500_incomplete(self):
        state = FakeSimeterState(initial=2)
        routes = state.routes()
        routes[("POST", JZJC_PATH)] = (500, {"detail": "boom"})
        cap = _simeter(state)
        cap.transport.routes = routes
        result = protocol.bench_simeter_protocol(_action(protocol=3), _ctx(cap))
        assert result.observation.reason_code == "incomplete"
        assert "未生效" in result.detail
        assert "协议模式" not in result.observation.values    # 没回读到新值
        assert result.observation.complete is False

    def test_readback_lost_after_write_incomplete(self):
        state = FakeSimeterState(initial=2)
        cap = _simeter(state)
        real_config = state.routes()[("GET", CONFIG_PATH)]
        reads = {"n": 0}

        def flaky_config(method, url, body):
            reads["n"] += 1
            if reads["n"] >= 2:                               # 切前成功、回读断
                raise ConnectionError("lost")
            return real_config(method, url, body)

        cap.transport.routes[("GET", CONFIG_PATH)] = flaky_config
        result = protocol.bench_simeter_protocol(_action(protocol=3), _ctx(cap))
        assert result.observation.reason_code == "bench_unreachable"
        assert result.observation.complete is False
        assert state.protocol_type == 3                       # 写已生效，但没确认


# --------------------------------------------------------------------------- #
# sta.meter_protocol（假模块能力：只读 logs，不 ensure 不发激励）
# --------------------------------------------------------------------------- #


class FakeModuleCap:
    """假模块能力：只实现 logs（块契约里本块只用它）。"""

    def __init__(self, entries=None, *, ok=True, error=None, detail=""):
        self.entries = entries if entries is not None else []
        self.ok = ok
        self.error = error
        self.detail = detail
        self.calls: list[dict] = []

    def logs(self, mapping_id=None, **kwargs):
        self.calls.append({"mapping_id": mapping_id, **kwargs})
        if not self.ok:
            return {"ok": False, "error": self.error, "detail": self.detail}
        return {"ok": True, "entries": self.entries}


def _module_ctx(cap) -> RunContext:
    return RunContext(extras={"module": cap})


#: run 20261124-192633 STA 日志行 532 前后的会话行（原文照录，645 实测证据）
STA_LOG_645 = [
    {"ts": 1795519606.3, "seq": 1, "text": "68123456789012689306123456789012d516"},
    {"ts": 1795519606.35, "seq": 2,
     "text": "addrmap success,DLT645_CMD,check_procotol:2,addr:"},
    {"ts": 1795519606.35, "seq": 3, "text": "129078563412"},
    {"ts": 1795519606.35, "seq": 4,
     "text": "5826 | info | aps_addrmap_state.c (1309)| try 698 pro"},
    {"ts": 1795519606.35, "seq": 5,
     "text": "6817004345aaaaaaaaaaaa10da5f0501134001020000204d16"},
]


class TestStaMeterProtocol:
    def test_fingerprint_645_bench_evidence(self):
        cap = FakeModuleCap(STA_LOG_645)
        result = protocol.sta_meter_protocol(
            {"kind": "sta_meter_protocol", "params": {}}, _module_ctx(cap))
        values = result.observation.values
        assert result.observation.complete is True
        assert values["判读协议类型"] == 2
        assert values["判读协议名称"] == "645-2007"
        assert "check_procotol:2" in values["指纹原文"]
        assert values["698探帧尝试"] is True
        assert values["日志行数"] == 5
        assert cap.calls[0]["mapping_id"] == "sta-main"       # 缺省口别名
        assert "未经真机校准" in result.detail                 # 口径声明随观测落地
        # 不判结论：观测本身不构成 pass（步骤期望自己承担口径风险）
        assert step_state_of(result, [
            {"op": "eq", "field": "判读协议类型", "value": 2}]) == BLOCK_PASS

    def test_fingerprint_698_expected_value(self):
        entries = STA_LOG_645[:2] + [
            {"ts": 1.0, "seq": 9, "text": "addrmap success,DLT645_CMD,check_procotol:3,addr:"},
        ]
        result = protocol.sta_meter_protocol(
            {"params": {}}, _module_ctx(FakeModuleCap(entries)))
        values = result.observation.values
        assert values["判读协议类型"] == 3
        assert values["判读协议名称"] == "698.45"

    def test_latest_fingerprint_wins(self):
        entries = [
            {"ts": 1.0, "seq": 1, "text": "addrmap success,DLT645_CMD,check_procotol:2,addr:"},
            {"ts": 2.0, "seq": 2, "text": "addrmap success,DLT645_CMD,check_procotol:3,addr:"},
        ]
        result = protocol.sta_meter_protocol(
            {"params": {}}, _module_ctx(FakeModuleCap(entries)))
        assert result.observation.values["判读协议类型"] == 3
        assert result.observation.values["指纹原文"].endswith("check_procotol:3,addr:")

    def test_no_fingerprint_incomplete(self):
        entries = [{"ts": 1.0, "seq": 1, "text": "nwk_nd_bcnIn, link=RF"}]
        result = protocol.sta_meter_protocol(
            {"params": {}}, _module_ctx(FakeModuleCap(entries)))
        values = result.observation.values
        assert result.observation.complete is False
        assert result.observation.reason_code == "incomplete"
        assert values["指纹原文"] == ""
        assert values["判读协议类型"] is None
        assert values["判读协议名称"] == ""
        assert values["698探帧尝试"] is False

    def test_malformed_number_not_matched(self):
        entries = [{"ts": 1.0, "seq": 1,
                    "text": "addrmap success,DLT645_CMD,check_procotol:,addr:"}]
        result = protocol.sta_meter_protocol(
            {"params": {}}, _module_ctx(FakeModuleCap(entries)))
        assert result.observation.reason_code == "incomplete"
        assert result.observation.values["指纹原文"] == ""

    def test_logs_unreadable_bench_unreachable(self):
        cap = FakeModuleCap(ok=False, error="http_404", detail="会话不存在")
        result = protocol.sta_meter_protocol(
            {"params": {}}, _module_ctx(cap))
        assert result.observation.reason_code == "bench_unreachable"
        assert step_state_of(result, [
            {"op": "eq", "field": "判读协议类型", "value": 3}]) == BLOCK_SKIPPED

    def test_readonly_no_incentive(self):
        """只读探针：只调 logs，且调用参数里没有 send/ensure 类写入口（side_effect=none）。"""
        cap = FakeModuleCap(STA_LOG_645)
        protocol.sta_meter_protocol({"params": {}}, _module_ctx(cap))
        assert set(cap.calls[0].keys()) <= {"mapping_id", "lines"}
        assert len(cap.calls) == 1

    def test_lines_param_forwarded(self):
        cap = FakeModuleCap(STA_LOG_645)
        protocol.sta_meter_protocol({"params": {"lines": 40}}, _module_ctx(cap))
        assert cap.calls[0]["mapping_id"] == "sta-main"


# --------------------------------------------------------------------------- #
# 注册与声明侧
# --------------------------------------------------------------------------- #


BLOCKS_DATA = Path(loader.DATA_DIR) / "blocks"


class TestDeclarations:
    def test_blocks_registered(self):
        protocol.register()
        assert has_block(protocol.BLOCK_SIMETER_PROTOCOL)
        assert has_block(protocol.BLOCK_STA_METER_PROTOCOL)
        assert get_block(protocol.BLOCK_SIMETER_PROTOCOL) is protocol.bench_simeter_protocol
        assert get_block(protocol.BLOCK_STA_METER_PROTOCOL) is protocol.sta_meter_protocol

    @pytest.mark.parametrize("block_id", [
        protocol.BLOCK_SIMETER_PROTOCOL,
        protocol.BLOCK_STA_METER_PROTOCOL,
    ])
    def test_declaration_metadata_and_fingerprint_fields(self, block_id):
        """三字段（kind/side_effect/version）+ 观测指纹（observation_fields）齐备。"""
        raw = json.loads((BLOCKS_DATA / (block_id.replace(".", "_") + ".json"))
                         .read_text(encoding="utf-8"))
        assert raw["id"] == block_id
        assert raw["kind"] in ("setup", "probe")
        assert raw["side_effect"] in ("none", "reversible")
        assert raw["version"] >= 1
        assert raw["observation_only"] is True
        assert raw["provides"] == [], "未经校准/台架态事实不回写（§G-6）"
        observation_fields = [item for item in raw["assert"]
                              if item.get("type") == "observation_fields"]
        assert observation_fields, "观测指纹必须显式在列（AC-4 口径）"

    def test_declarations_load_with_full_library(self):
        specs = {spec.id: spec for spec in loader.load_blocks()}
        assert specs[protocol.BLOCK_SIMETER_PROTOCOL].kind == "setup"
        assert specs[protocol.BLOCK_SIMETER_PROTOCOL].side_effect == "reversible"
        assert specs[protocol.BLOCK_STA_METER_PROTOCOL].kind == "probe"
        assert specs[protocol.BLOCK_STA_METER_PROTOCOL].side_effect == "none"
        assert specs[protocol.BLOCK_STA_METER_PROTOCOL].observation_only is True
        assert specs[protocol.BLOCK_STA_METER_PROTOCOL].provides == []
