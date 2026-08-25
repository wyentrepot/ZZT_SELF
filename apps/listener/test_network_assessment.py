"""network_assessment 单元测试：判定规则、NID/CCO MAC 提取、周期扫描、网络隔离。"""
import json
import unittest

from listener import network_assessment as na


def build_gw_frame(nid=0x947F69, frm_type=2, tei=3, mpdu_bytes=b"", padding=16):
    """构造一条 GW 侦听台封装帧 hex。

    nid 按 FCH[1..3] 小端存放；mpdu 直接拼在 FCH 后，补 padding 字节。
    """
    header = bytes(20)
    fch = bytearray(16)
    fch[0] = frm_type & 0x07
    fch[1] = nid & 0xFF
    fch[2] = (nid >> 8) & 0xFF
    fch[3] = (nid >> 16) & 0xFF
    fch[8] = tei & 0xFF
    fch[9] = (tei >> 8) & 0xFF
    body = header + bytes(fch) + bytes(mpdu_bytes) + bytes(padding)
    raw = b"\x7E\xFF\x02" + body + b"\x7E"
    return raw.hex(" ").upper()


def build_central_beacon(nid=0x947F69, cco_mac=b"\x26\x09\x13\x46\x60\x00",
                         cnt=0, seq=1, tei=1, extra=b""):
    """构造一条中央信标帧（定界符0 + 信标类型2 + CCO MAC + 周期计数）。"""
    mpdu = bytearray()
    mpdu.append(0x02 | 0x08)  # 低3位=2 中央信标，bit3=组网完成
    mpdu.append(seq)          # 组网序列号
    mpdu.extend(cco_mac)      # CCO MAC 6 字节
    mpdu.extend(cnt.to_bytes(4, "little"))  # 信标周期计数
    mpdu.extend(extra)
    return build_gw_frame(nid=nid, frm_type=0, tei=tei, mpdu_bytes=bytes(mpdu))


def make_record(ms, sta, result=0, count=1, error=None, nid=0x947F69):
    return {
        "time_seconds": ms,
        "station_key": sta,
        "response_result": result,
        "report_count": count,
        "application_error": error,
        "nid": nid,
    }


class RatingRuleTests(unittest.TestCase):
    """三级判定规则：成功率 98/95/85，离线率 2/5/15，汇总规则。"""

    def test_success_rate_thresholds(self):
        self.assertEqual(na._classify(98.0, None)[0], na.HEALTHY)
        self.assertEqual(na._classify(97.5, None)[0], na.DEGRADED)
        self.assertEqual(na._classify(90.0, None)[0], na.DEGRADED)
        self.assertEqual(na._classify(85.0, None)[0], na.FAULT)

    def test_offline_rate_thresholds(self):
        self.assertEqual(na._classify(None, 2.0)[0], na.HEALTHY)
        self.assertEqual(na._classify(None, 5.0)[0], na.DEGRADED)
        self.assertEqual(na._classify(None, 15.0)[0], na.FAULT)

    def test_offline_undetermined_ignored(self):
        # 离线率无法判定时仅用成功率
        self.assertEqual(na._classify(99.0, None)[0], na.HEALTHY)
        self.assertEqual(na._classify(85.0, None)[0], na.FAULT)

    def test_aggregate_rules(self):
        self.assertEqual(na._aggregate_level([na.HEALTHY, na.HEALTHY]), na.HEALTHY)
        self.assertEqual(
            na._aggregate_level([na.HEALTHY, na.DEGRADED]), na.DEGRADED
        )
        self.assertEqual(
            na._aggregate_level([na.HEALTHY, na.DEGRADED, na.FAULT]), na.FAULT
        )

    def test_bucket_rating_via_assess_periods(self):
        # 单桶 20 帧，18 成功 → 90% 亚健康
        records = [make_record(1_000 + i * 100, f"STA{i % 10}") for i in range(18)]
        records += [make_record(1_000 + i * 100, f"STA{i % 10}", result=1)
                    for i in range(18, 20)]
        cycles = na.assess_periods(records, 3_000, {f"STA{i}" for i in range(10)})
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["success_rate"], 90.0)
        self.assertEqual(cycles[0]["level"], na.DEGRADED)

    def test_offline_weak_proxy(self):
        # active 10 台，本桶只有 5 台上报 → 离线率 50%，故障
        records = [make_record(1_000 + i, f"STA{i}", count=1) for i in range(5)]
        cycles = na.assess_periods(records, 3_000, {f"STA{i}" for i in range(10)})
        self.assertEqual(cycles[0]["offline_rate"], 50.0)
        self.assertEqual(cycles[0]["level"], na.FAULT)


class ExtractionTests(unittest.TestCase):
    """extract_nid / extract_cco_mac 构造样例自检。"""

    def test_extract_nid_known_frame(self):
        # 真实样例首帧（ACK，SNID=00947F69）
        raw = (
            "7E FF 02 FF 24 6B 75 5B 67 05 E5 03 B9 D0 49 EC 00 39 00 10 00 01 00 "
            "02 69 7F 94 10 01 40 03 01 00 00 00 00 BD 72 F2 87 7B A5 C9 7E"
        )
        self.assertEqual(na.extract_nid(raw), 0x947F69)

    def test_extract_nid_constructed(self):
        self.assertEqual(na.extract_nid(build_gw_frame(nid=0x123456)), 0x123456)
        self.assertIsNone(na.extract_nid("not hex"))

    def test_extract_cco_mac_central_beacon(self):
        raw = build_central_beacon(cco_mac=b"\x26\x09\x13\x46\x60\x00")
        self.assertEqual(na.extract_cco_mac(raw), "26-09-13-46-60-00")

    def test_extract_cco_mac_non_beacon(self):
        # ACK 帧无 CCO MAC
        self.assertIsNone(na.extract_cco_mac(build_gw_frame(frm_type=2)))

    def test_extract_cco_mac_proxy_beacon(self):
        # 代理信标（类型1）不应识别为 CCO MAC
        mpdu = bytes([0x01 | 0x08]) + b"\x01" + b"\x00" * 10
        raw = build_gw_frame(frm_type=0, mpdu_bytes=mpdu)
        self.assertIsNone(na.extract_cco_mac(raw))


class BeaconPeriodTests(unittest.TestCase):
    """scan_beacon_periods：20 帧间隔 3.0s 估出≈3.0s。"""

    def test_scan_3_seconds(self):
        frames = []
        for i in range(20):
            t = f"10:{i // 60:02d}:{(i * 3) % 60:02d}.{(i * 3) % 1:03d}"
            # 直接生成绝对秒内的合法时间：用 08:00:00 起步 + i*3 秒
            seconds = 8 * 3600 + i * 3
            t = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}.000"
            frames.append((t, build_central_beacon(cnt=i)))
        result = na.scan_beacon_periods(frames)
        self.assertEqual(result["method"], "central_beacon")
        self.assertIsNotNone(result["beacon_period_ms"])
        self.assertAlmostEqual(result["beacon_period_ms"], 3_000, delta=100)
        self.assertGreater(result["confidence"], 0.5)

    def test_scan_dedupes_duplicate_captures(self):
        # 同一信标（同 cnt）重复抓包 3 次，只算一次
        frames = []
        base = 8 * 3600
        for i in range(6):
            for dup in range(3):
                t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.{dup:03d}"
                frames.append((t, build_central_beacon(cnt=i)))
            base += 3  # 3 秒一跳
        result = na.scan_beacon_periods(frames)
        self.assertAlmostEqual(result["beacon_period_ms"], 3_000, delta=100)

    def test_scan_undetected(self):
        # 无信标帧 → 不报错，周期为 None
        frames = [(f"00:00:0{i}.000", build_gw_frame(frm_type=2)) for i in range(10)]
        result = na.scan_beacon_periods(frames)
        self.assertIsNone(result["beacon_period_ms"])
        self.assertEqual(result["method"], "undetected")


class NetworkIsolationTests(unittest.TestCase):
    """两个不同 NID 帧流分别估出各自周期，不混算。"""

    def test_two_networks_not_mixed(self):
        frames, records = [], []
        # 网络 A：周期 3s
        for i in range(12):
            seconds = 8 * 3600 + i * 3
            t = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}.000"
            frames.append((t, build_central_beacon(nid=0x947F69, cnt=i)))
        # 网络 B：周期 5s
        for i in range(12):
            seconds = 8 * 3600 + i * 5
            t = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}.000"
            frames.append((t, build_central_beacon(nid=0x000456, cnt=i)))

        for i in range(24):
            nid = 0x947F69 if i % 2 == 0 else 0x000456
            records.append(make_record(8 * 3600_000 + i * 3_000, f"STA{i}", nid=nid))

        # 帧转 dict 接口
        frame_dicts = [{"log_time": t, "raw_hex": h} for t, h in frames]
        result = na.assess_by_network(frame_dicts, records)
        self.assertEqual(len(result["networks"]), 2)
        periods = {n["nid"]: n["beacon_period_ms"] for n in result["networks"]}
        self.assertAlmostEqual(periods[0x947F69], 3_000, delta=100)
        self.assertAlmostEqual(periods[0x000456], 5_000, delta=100)

    def test_cco_mac_joins_frames_of_same_network(self):
        # 同一 NID：信标帧带 MAC，普通数据帧不带，应合并为一个网络
        frames = [
            {"log_time": "00:00:01.000", "raw_hex": build_central_beacon(nid=0x123456)},
            {"log_time": "00:00:01.100", "raw_hex": build_gw_frame(nid=0x123456, frm_type=2)},
        ]
        records = [make_record(1_000, "STA1", nid=0x123456)]
        result = na.assess_by_network(frames, records)
        self.assertEqual(len(result["networks"]), 1)
        self.assertEqual(result["networks"][0]["cco_mac"], "26-09-13-46-60-00")


class StabilityDimensionTests(unittest.TestCase):
    """稳定性维度：帧型统计、代理变更/关联请求占比判级、时长门槛、结构兼容。"""

    @staticmethod
    def _frame(frm_type, summary_shape="simple"):
        """构造一帧（带 summary_json，兼容两种 FrmType 存放结构）。"""
        if summary_shape == "direct":
            summary = json.dumps({"FrmType": frm_type})
        else:
            summary = json.dumps({"simple": {"FrmType": frm_type}})
        return {
            "log_time": "08:00:00.000",
            "raw_hex": build_gw_frame(frm_type=2),
            "summary_json": summary,
        }

    def _frames(self, proxy_change=0, assoc=0, total=100, shape="simple"):
        frames = [self._frame(na.FRMTYPE_PROXY_CHANGE, shape) for _ in range(proxy_change)]
        frames += [self._frame(na.FRMTYPE_ASSOC, shape) for _ in range(assoc)]
        frames += [self._frame("ACK", shape) for _ in range(total - proxy_change - assoc)]
        return frames

    def test_count_frame_types_proxy_change(self):
        frames = self._frames(proxy_change=9)
        stats = na.count_frame_types(frames)
        self.assertEqual(stats[na.FRMTYPE_PROXY_CHANGE], 9)
        self.assertEqual(stats["ACK"], 91)
        self.assertEqual(sum(stats.values()), 100)

    def test_count_frame_types_unknown_on_missing(self):
        # summary_json 缺失/非 JSON/无 FrmType → 计入 UNKNOWN
        frames = [
            {"log_time": "08:00:00.000", "raw_hex": build_gw_frame(frm_type=2)},
            {"log_time": "08:00:00.000", "raw_hex": build_gw_frame(frm_type=2),
             "summary_json": "not-json"},
            {"log_time": "08:00:00.000", "raw_hex": build_gw_frame(frm_type=2),
             "summary_json": json.dumps({"foo": 1})},
            self._frame("ACK"),
        ]
        stats = na.count_frame_types(frames)
        self.assertEqual(stats["UNKNOWN"], 3)
        self.assertEqual(stats["ACK"], 1)

    def test_assess_stability_proxy_change_degraded(self):
        # 100 帧里 9 个代理变更 → 9%>8% → 降级
        stats = na.count_frame_types(self._frames(proxy_change=9))
        result = na.assess_stability(stats, 3 * 3600, 3_000)
        self.assertTrue(result["enabled"])
        self.assertEqual(result["proxy_change_count"], 9)
        self.assertAlmostEqual(result["proxy_change_ratio"], 9.0)
        self.assertEqual(result["level"], na.DEGRADED)

    def test_assess_stability_proxy_change_healthy(self):
        # 100 帧里 7 个代理变更 → 7%<=8% → 健康
        stats = na.count_frame_types(self._frames(proxy_change=7))
        result = na.assess_stability(stats, 3 * 3600, 3_000)
        self.assertEqual(result["level"], na.HEALTHY)
        self.assertIsNone(result["reason"])

    def test_assess_stability_duration_gate(self):
        # 1.5h <= 7200s → enabled=False，只统计不判级
        stats = na.count_frame_types(self._frames(proxy_change=20))
        result = na.assess_stability(stats, 1.5 * 3600, 3_000)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "log_too_short")
        self.assertEqual(result["level"], na.HEALTHY)
        self.assertEqual(result["proxy_change_count"], 20)  # 仍统计

    def test_assess_stability_assoc_degraded(self):
        # 关联请求 6/100=6%>5% → 降级
        stats = na.count_frame_types(self._frames(assoc=6))
        result = na.assess_stability(stats, 3 * 3600, 3_000)
        self.assertEqual(result["level"], na.DEGRADED)
        self.assertAlmostEqual(result["assoc_ratio"], 6.0)

    def test_assess_stability_assoc_healthy(self):
        # 关联请求 4/100=4%<=5% → 健康
        stats = na.count_frame_types(self._frames(assoc=4))
        result = na.assess_stability(stats, 3 * 3600, 3_000)
        self.assertEqual(result["level"], na.HEALTHY)

    def test_count_frame_types_structure_compat(self):
        # {"simple":{"FrmType":...}} 与直接 {"FrmType":...} 都能统计
        frames = [
            self._frame("ACK", "simple"),
            self._frame("ACK", "direct"),
            self._frame(na.FRMTYPE_ASSOC, "simple"),
            self._frame(na.FRMTYPE_ASSOC, "direct"),
        ]
        stats = na.count_frame_types(frames)
        self.assertEqual(stats["ACK"], 2)
        self.assertEqual(stats[na.FRMTYPE_ASSOC], 2)

    def test_assess_by_network_exposes_stability(self):
        # 网络级评估：summary.stability 透出、cycles 带 stability_level
        base = 8 * 3600
        frame_dicts = []
        for i in range(10):
            t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.000"
            frame_dicts.append({"log_time": t, "raw_hex": build_central_beacon(cnt=i)})
            base += 3  # 3 秒一跳，保证信标周期可识别
        frame_dicts[0] = {**frame_dicts[0],
                          "summary_json": json.dumps({"simple": {"FrmType": na.FRMTYPE_PROXY_CHANGE}})}
        records = [make_record(8 * 3600_000 + i * 3_000, f"STA{i}", nid=0x947F69)
                   for i in range(12)]
        result = na.assess_by_network(frame_dicts, records)
        network = result["networks"][0]
        self.assertIn("stability", network["summary"])
        self.assertIn("stability_level", network["cycles"][0])
        self.assertEqual(network["summary"]["stability"]["proxy_change_count"], 1)


if __name__ == "__main__":
    unittest.main()
