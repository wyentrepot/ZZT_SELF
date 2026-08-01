"""应用层分析服务：把 DLL 摘要中的有界应用字节交给 Python 适配器富化。

DLL 负责 sniffer 信封/FCH/MAC/TEI 与精确 APS 边界，输出 APP_PORT/APP_ID/
APP_RAW；本服务只消费 APP_RAW，用 DualMode43Adapter 解出分钟采集结构化结果，
合并回简单摘要（FrmType 提升 + application 字典）。适配器失败时保留原 FrmType
并记录 application_error，保证建索引不中断。
"""
from parser_lib.adapters.adapter_dualmode import DualMode43Adapter

MINUTE_TYPES = {
    "00E2": "分钟采集任务配置",
    "00E3": "分钟采集数据读取",
    "00E4": "分钟采集数据上报",
}


def _serialize_field(field) -> dict:
    return {
        "name": field.name,
        "value": field.value,
        "unit": field.unit,
        "hex": field.hex,
        "raw": field.raw,
        "desc": field.desc,
    }


def _serialize_frame(frame) -> dict:
    return {
        "structure": frame.structure,
        "address": frame.address,
        "raw_hex": frame.raw_hex,
        "fields": [_serialize_field(f) for f in frame.fields],
        "items": [_serialize_field(f) for f in frame.items],
        "nested": [_serialize_frame(n) for n in frame.nested],
        "warnings": list(frame.warnings),
    }


class ApplicationAnalysisService:
    def __init__(self):
        # 复用同一适配器实例（纯 Python，无共享可变状态）
        self._adapter = DualMode43Adapter()

    def decode(self, app_hex: str) -> dict:
        frame = self._adapter.decode(bytes.fromhex(app_hex))
        return _serialize_frame(frame)

    def enrich_summary(self, simple: dict) -> dict:
        app_id = simple.get("APP_ID")
        app_raw = simple.get("APP_RAW")
        if not app_id or app_id not in MINUTE_TYPES or not app_raw:
            return simple
        try:
            application = self.decode(app_raw)
        except Exception as exc:  # 适配器失败不中断建索引
            simple["application_error"] = str(exc)
            return simple

        simple["application"] = application
        simple["BaseFrmType"] = simple.get("FrmType")
        simple["FrmType"] = MINUTE_TYPES[app_id]
        return simple
