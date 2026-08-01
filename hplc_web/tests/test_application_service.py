"""应用层分析服务测试：把 DLL 摘要中的 APP_RAW 交给 DualMode43Adapter 富化。"""
import unittest

from hplc_web.application_service import ApplicationAnalysisService

E4_APP_HEX = (
    "11E400000132C40000005E00"
    "013401001412230702005523310726014C00"
    "6834010014122368910633343435A456AF16"
    "683401001412236891063335343532321A16"
    "683401001412236891063336343532321B16"
    "6834010014122368910A33323435A456323232327916"
)


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


if __name__ == "__main__":
    unittest.main()
