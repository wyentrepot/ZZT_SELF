"""应用层分析服务测试：把 DLL 摘要中的 APP_RAW 交给 DualMode43Adapter 富化。"""
import unittest

from shared.application_service import ApplicationAnalysisService

E4_APP_HEX = (
    "11E400000132C40000005E00"
    "013401001412230702005523310726014C00"
    "6834010014122368910633343435A456AF16"
    "683401001412236891063335343532321A16"
    "683401001412236891063336343532321B16"
    "6834010014122368910A33323435A456323232327916"
)

# 真实并发抄表帧（0x0003，DATA 承载 698.45 帧），来源 test_dualmode.py
# test_real_concurrent_meter_frame_starts_at_1103
METER_0003_APP_HEX = (
    "110300000102630859050100688400c30535378109003010f18390006b850337"
    "5002020008002021020000200104000020000200002001020000200402000020"
    "0a02000000100201000020020101011c07ea061d0e1e00050000000001011208"
    "a30101050000000001020500000000050000000001021003e81003e806000000"
    "0006000000000000010004d0c1a502010016"
)


def _meter_app_hex(msg_id_hex: str, proto_type: int, data_hex: str, seq=0x0001):
    """构造抄表类 APP_RAW：4 字节通用头 + 抄表业务头(8B) + DATA。

    业务头：协议版本号=1, 报文头长度=8, 规约类型, 转发数据长度(12bit LE),
    报文序号, 设备超时, 选项字。
    """
    data = bytes.fromhex(data_hex)
    ver = 1
    header_len = 8
    b0 = ver | ((header_len & 0x03) << 6)
    b1 = (header_len >> 2) & 0x0F
    b2 = (proto_type & 0x0F) | (((len(data) >> 8) & 0x0F) << 4)
    b3 = len(data) & 0xFF
    head = bytes([b0, b1, b2, b3, seq & 0xFF, (seq >> 8) & 0xFF, 0x0A, 0x00])
    return ("11" + msg_id_hex + "0000").upper() + (head + data).hex().upper()


# 一条 DL/T645 数据帧（读取应答示例）
_F645_DATA = "6812345678901268910800000100123456783416"


def _field(result, name):
    for f in result["fields"]:
        if f["name"] == name:
            return f
    return None


def _nested(result):
    return result.get("nested", [])


class ApplicationAnalysisServiceTests(unittest.TestCase):
    def test_decode_returns_json_safe_structured_result(self):
        result = ApplicationAnalysisService().decode(E4_APP_HEX)

        self.assertEqual(result["structure"], "双模4-3")
        fields = {f["name"]: f for f in result["fields"]}
        self.assertEqual(fields["报文ID"]["raw"], 0x00E4)
        self.assertEqual(fields["分钟采集类型"]["value"], "主动上报")
        self.assertEqual(fields["任务号"]["raw"], 7)
        self.assertEqual(fields["数据长度"]["raw"], 76)
        self.assertEqual(len(result["nested"]), 4)
        self.assertTrue(all(n["structure"] == "645" for n in result["nested"]))

    def test_enrich_summary_promotes_minute_type_and_keeps_base_type(self):
        simple = {
            "FrmType": "APS",
            "APP_PORT": "11",
            "APP_ID": "00E4",
            "APP_RAW": E4_APP_HEX,
        }

        out = ApplicationAnalysisService().enrich_summary(simple)

        self.assertEqual(out["FrmType"], "分钟采集数据上报")
        self.assertEqual(out["BaseFrmType"], "APS")
        self.assertEqual(out["application"]["structure"], "双模4-3")
        self.assertEqual(len(out["application"]["nested"]), 4)

    def test_enrich_summary_promotes_e2_and_e3_types(self):
        svc = ApplicationAnalysisService()
        e2 = svc.enrich_summary({"FrmType": "APS", "APP_ID": "00E2", "APP_RAW": "11E200000104"})
        e3 = svc.enrich_summary({"FrmType": "APS", "APP_ID": "00E3", "APP_RAW": "11E300000105"})
        self.assertEqual(e2["FrmType"], "分钟采集任务配置")
        self.assertEqual(e3["FrmType"], "分钟采集数据读取")

    def test_enrich_summary_leaves_unknown_app_id_unchanged(self):
        simple = {"FrmType": "APS", "APP_ID": "00A1", "APP_RAW": "11A1000000"}
        svc = ApplicationAnalysisService()

        out = svc.enrich_summary(simple)

        self.assertEqual(out, simple)
        self.assertNotIn("application", out)

    def test_enrich_summary_keeps_aps_and_reports_error_on_adapter_failure(self):
        simple = {"FrmType": "APS", "APP_ID": "00E4", "APP_RAW": "11E4"}

        out = ApplicationAnalysisService().enrich_summary(simple)

        self.assertEqual(out["FrmType"], "APS")
        self.assertIn("application_error", out)

    # ---- 抄表帧（0x0001/0x0002/0x0003）富化 ----

    def test_enrich_summary_routes_meter_0003_real_frame(self):
        """真实并发抄表帧：APP_RAW 走 Python 适配器，递归解出内嵌 698.45 帧。"""
        simple = {
            "FrmType": "终端主动并发抄表",  # DLL 侧已识别（V1.0.23 统一命名后）
            "APP_PORT": "11",
            "APP_ID": "0003",
            "APP_RAW": METER_0003_APP_HEX,
        }

        out = ApplicationAnalysisService().enrich_summary(simple)

        self.assertEqual(out["FrmType"], "终端主动并发抄表")
        self.assertEqual(out["BaseFrmType"], "终端主动并发抄表")  # 保留 DLL 原值
        application = out["application"]
        self.assertEqual(application["structure"], "双模4-3")
        self.assertEqual(_field(application, "报文ID")["raw"], 0x0003)
        self.assertEqual(_field(application, "协议版本号")["raw"], 1)
        self.assertEqual(_field(application, "报文头长度")["raw"], 8)
        self.assertEqual(_field(application, "转发数据规约类型")["raw"], 3)
        nested = _nested(application)
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested[0]["structure"], "698.45")

    def test_enrich_summary_routes_meter_0001_and_0002(self):
        """构造 0001/0002 抄表帧：645 DATA 递归解出内嵌 645 帧。"""
        svc = ApplicationAnalysisService()
        for msg_id, expected_type in (
            ("0001", "终端主动抄表"),
            ("0002", "路由主动抄表"),
        ):
            app_hex = _meter_app_hex(msg_id, 2, _F645_DATA)
            simple = {
                "FrmType": "APS",
                "APP_PORT": "11",
                "APP_ID": msg_id,
                "APP_RAW": app_hex,
            }

            out = svc.enrich_summary(simple)

            self.assertEqual(out["FrmType"], expected_type)
            self.assertEqual(out["BaseFrmType"], "APS")
            self.assertEqual(out["application"]["structure"], "双模4-3")
            self.assertEqual(len(_nested(out["application"])), 1)
            self.assertEqual(_nested(out["application"])[0]["structure"], "645")

    def test_enrich_summary_meter_adapter_failure_keeps_base_type(self):
        """抄表帧 APP_RAW 无法解析时不提升类型，保留 application_error。"""
        simple = {
            "FrmType": "终端主动并发抄表",
            "APP_PORT": "11",
            "APP_ID": "0003",
            "APP_RAW": "110300",  # 不足 4 字节，decode 读 raw[3] 越界
        }

        out = ApplicationAnalysisService().enrich_summary(simple)

        self.assertEqual(out["FrmType"], "终端主动并发抄表")
        self.assertIn("application_error", out)


if __name__ == "__main__":
    unittest.main()
