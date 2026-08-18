"""A2：SourceAdapter 适配器契约测试（docs/03 §5）。

覆盖：接口存在、默认未实现抛错、stop() 幂等、错误统一类型、
生命周期 start→collect→stop。
"""
import pytest

from test_automation.adapters import SourceAdapter, AdapterError, AdapterHealth


class DummyAdapter(SourceAdapter):
    def __init__(self):
        self.started = False
        self.stopped = 0
        self.collected = 0

    def start(self, run_context):
        self.started = True

    def collect(self, evidence_sink):
        self.collected += 1
        return []

    def stop(self):
        self.stopped += 1

    def health(self):
        return AdapterHealth(ok=True, message="ok")


class TestSourceAdapterInterface:
    def test_has_all_methods(self):
        for name in ("start", "collect", "stop", "health"):
            assert hasattr(SourceAdapter, name), f"缺少方法 {name}"

    def test_abstract_base_not_instantiable_directly(self):
        # SourceAdapter 是抽象基类：直接实例化应报错（未实现全部抽象方法）
        with pytest.raises(TypeError):
            SourceAdapter()

    def test_lifecycle_start_collect_stop(self):
        a = DummyAdapter()
        a.start({"case_id": "c1"})
        assert a.started is True
        a.collect(object())
        assert a.collected == 1
        a.stop()
        assert a.stopped == 1

    def test_stop_is_idempotent(self):
        a = DummyAdapter()
        a.stop()
        a.stop()
        a.stop()
        assert a.stopped == 3  # 每次调用都被执行且不抛错

    def test_health_returns_health(self):
        a = DummyAdapter()
        h = a.health()
        assert h.ok is True
        assert h.message == "ok"


class TestAdapterError:
    def test_error_carries_code_and_message(self):
        e = AdapterError("SERIAL_READ_FAILED", "读取失败")
        assert e.code == "SERIAL_READ_FAILED"
        assert e.message == "读取失败"
        assert str(e) == "SERIAL_READ_FAILED: 读取失败"

    def test_error_is_exception(self):
        assert issubclass(AdapterError, Exception)
