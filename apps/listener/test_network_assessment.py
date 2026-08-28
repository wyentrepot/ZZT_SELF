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


class BeaconParamPriorityTests(unittest.TestCase):
    """scan_beacon_periods 参数优先：中央信标 Detail「信标周期Xms」为权威周期。

    协议「信标周期」= 同相线 CCO 中央信标重复间隔；相邻到达间隔在三相交错
    网络里是短间隔，不能代表同相线周期，故 Detail 参数优先（用户拍板）。
    """

    SLOT_PARAM_DETAIL = (
        "|关联标志|组网seq:238|信标条目:4|站点能力[全网]|路由参数[CCO "
        "|时隙分配[信标周期6000ms 信标时隙长度16ms RF信标时隙长度16ms "
        "CSMA时隙大小500ms]|无线路由参数"
    )

    def _beacon_frame(self, seconds, cnt):
        t = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}.000"
        return {
            "log_time": t,
            "raw_hex": build_central_beacon(cnt=cnt),
            "summary_json": json.dumps(
                {"FrmType": "中央信标", "Detail": self.SLOT_PARAM_DETAIL}
            ),
        }

    def test_param_wins_over_inter_arrival(self):
        # 5 条中央信标帧，Detail 带「信标周期6000ms」；时间戳故意 2s 一跳
        # （三相交错短间隔）→ 参数路径优先返回 6000 而非 2000
        base = 8 * 3600
        frames = [self._beacon_frame(base + i * 2, cnt=i) for i in range(5)]
        result = na.scan_beacon_periods(frames)
        self.assertEqual(result["method"], "beacon_param")
        self.assertEqual(result["beacon_period_ms"], 6000)
        self.assertEqual(result["sample_count"], 5)
        self.assertEqual(result["interval_count"], 0)
        self.assertAlmostEqual(result["confidence"], 5 / 8, delta=1e-3)

    def test_fallback_when_no_detail_param(self):
        # 纯 hex 无 summary_json → 无 Detail 参数 → 走到达间隔推算（central_beacon）
        frames = []
        base = 8 * 3600
        for i in range(10):
            t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.000"
            frames.append((t, build_central_beacon(cnt=i)))
            base += 2
        result = na.scan_beacon_periods(frames)
        self.assertEqual(result["method"], "central_beacon")
        self.assertAlmostEqual(result["beacon_period_ms"], 2_000, delta=100)

    def test_fallback_when_detail_missing_slot_config(self):
        # summary_json 存在但 Detail 无「时隙分配」参数 → 仍走到达间隔推算
        base = 8 * 3600
        frames = []
        for i in range(10):
            t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.000"
            frames.append({
                "log_time": t,
                "raw_hex": build_central_beacon(cnt=i),
                "summary_json": json.dumps(
                    {"FrmType": "中央信标", "Detail": "|组网seq:238|"}
                ),
            })
            base += 2
        result = na.scan_beacon_periods(frames)
        self.assertEqual(result["method"], "central_beacon")
        self.assertAlmostEqual(result["beacon_period_ms"], 2_000, delta=100)

    def test_param_below_min_falls_back(self):
        # Detail 信标周期 300ms < BEACON_PARAM_MIN_MS(500ms) → 视为解析异常，
        # 参数不采信，回退到达间隔推算
        base = 8 * 3600
        frames = []
        for i in range(10):
            t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.000"
            frames.append({
                "log_time": t,
                "raw_hex": build_central_beacon(cnt=i),
                "summary_json": json.dumps(
                    {"FrmType": "中央信标",
                     "Detail": "|时隙分配[信标周期300ms 信标时隙长度16ms "
                               "RF信标时隙长度16ms CSMA时隙大小500ms]|"}
                ),
            })
            base += 2
        result = na.scan_beacon_periods(frames)
        self.assertEqual(result["method"], "central_beacon")
        self.assertAlmostEqual(result["beacon_period_ms"], 2_000, delta=100)

    def _param_beacon_frames(self, period_ms, count=5, gap_s=2):
        """构造 count 条中央信标帧，Detail 统一带「信标周期{period_ms}ms」参数。"""
        detail = (
            f"|时隙分配[信标周期{period_ms}ms 信标时隙长度16ms "
            f"RF信标时隙长度16ms CSMA时隙大小500ms]|"
        )
        base = 8 * 3600
        frames = []
        for i in range(count):
            t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.000"
            frames.append({
                "log_time": t,
                "raw_hex": build_central_beacon(cnt=i),
                "summary_json": json.dumps(
                    {"FrmType": "中央信标", "Detail": detail}
                ),
            })
            base += gap_s
        return frames

    def test_param_14878ms_accepted(self):
        # 实测设备信标周期 14878ms（>10s），参数路径直接采信，不再越界回退到
        # 间隔推算（修复前该值超出 [1s,10s] 会回退算出错误的 1700ms）
        detail = (
            "|时隙分配[信标周期14878ms 信标时隙长度36ms RF信标时隙长度36ms "
            "CSMA时隙大小500ms]|"
        )
        base = 8 * 3600
        frames = []
        for i in range(5):
            t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.000"
            frames.append({
                "log_time": t,
                "raw_hex": build_central_beacon(cnt=i),
                "summary_json": json.dumps(
                    {"FrmType": "中央信标", "Detail": detail}
                ),
            })
            base += 2
        result = na.scan_beacon_periods(frames)
        self.assertEqual(result["method"], "beacon_param")
        self.assertEqual(result["beacon_period_ms"], 14878)
        self.assertEqual(result["sample_count"], 5)
        self.assertEqual(result["interval_count"], 0)

    def test_param_boundary_min_accepted(self):
        # 参数恰好 500ms（BEACON_PARAM_MIN_MS 下界，含）→ 采信
        result = na.scan_beacon_periods(self._param_beacon_frames(500))
        self.assertEqual(result["method"], "beacon_param")
        self.assertEqual(result["beacon_period_ms"], 500)

    def test_param_boundary_max_accepted(self):
        # 参数恰好 120000ms（BEACON_PARAM_MAX_MS 上界，含）→ 采信
        result = na.scan_beacon_periods(self._param_beacon_frames(120_000))
        self.assertEqual(result["method"], "beacon_param")
        self.assertEqual(result["beacon_period_ms"], 120_000)

    def test_assess_by_network_scan_method_beacon_param(self):
        # 网络级：帧带 Detail 参数时，scan_method 应为 beacon_param、周期取参数值
        base = 8 * 3600
        frame_dicts = []
        for i in range(5):
            frame_dicts.append(self._beacon_frame(base + i * 2, cnt=i))
        result = na.assess_by_network(frame_dicts, [])
        network = result["networks"][0]
        self.assertEqual(network["scan_method"], "beacon_param")
        self.assertEqual(network["beacon_period_ms"], 6000)


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


class SlotAndRouteChannelDimensionTests(unittest.TestCase):
    """B 档时隙占用 + C 档路由/信道：Detail 文本提取与判级、合并。"""

    SLOT_DETAIL = (
        "|时隙配置[信标周期2094ms 信标时隙长度16ms "
        "RF信标时隙长度16ms CSMA时隙大小500ms]|"
    )

    def test_extract_slot_fields(self):
        fields = na.extract_slot_fields(self.SLOT_DETAIL)
        self.assertEqual(fields, {
            "beacon_period_ms": 2094,
            "beacon_slot_ms": 16,
            "rf_beacon_slot_ms": 16,
            "csma_slot_ms": 500,
        })

    def test_extract_slot_fields_none(self):
        self.assertIsNone(na.extract_slot_fields("|组网seq:238|"))
        self.assertIsNone(na.extract_slot_fields(None))

    def test_extract_slot_fields_alloc_variant(self):
        # 实测 DLL Detail 用「时隙分配」而非「时隙配置」，两种写法都应能提取
        detail = (
            "|关联标志|组网seq:238|信标条目:4|站点能力[全网]|路由参数[CCO "
            "|时隙分配[信标周期2094ms 信标时隙长度16ms "
            "RF信标时隙长度16ms CSMA时隙大小500ms]|无线路由参数"
        )
        fields = na.extract_slot_fields(detail)
        self.assertEqual(fields["beacon_period_ms"], 2094)
        self.assertEqual(fields["beacon_slot_ms"], 16)
        self.assertEqual(fields["rf_beacon_slot_ms"], 16)
        self.assertEqual(fields["csma_slot_ms"], 500)

    def test_assess_slot_healthy(self):
        # 500/2094 ≈ 23.9% → 健康
        result = na.assess_slot({}, [na.extract_slot_fields(self.SLOT_DETAIL)])
        self.assertTrue(result["enabled"])
        self.assertAlmostEqual(result["csma_ratio"], 23.88, delta=0.1)
        self.assertEqual(result["csma_slot_ms"], 500)
        self.assertEqual(result["beacon_period_ms"], 2094)
        self.assertEqual(result["level"], na.HEALTHY)
        self.assertIsNone(result["reason"])

    def test_assess_slot_degraded(self):
        # 1300/2000 = 65% > 60% → 降级
        detail = ("|时隙配置[信标周期2000ms 信标时隙长度16ms "
                  "RF信标时隙长度16ms CSMA时隙大小1300ms]|")
        result = na.assess_slot({}, [na.extract_slot_fields(detail)])
        self.assertAlmostEqual(result["csma_ratio"], 65.0)
        self.assertEqual(result["level"], na.DEGRADED)

    def test_assess_slot_fault(self):
        # 1700/2000 = 85% > 80% → 故障
        detail = ("|时隙配置[信标周期2000ms 信标时隙长度16ms "
                  "RF信标时隙长度16ms CSMA时隙大小1700ms]|")
        result = na.assess_slot({}, [na.extract_slot_fields(detail)])
        self.assertAlmostEqual(result["csma_ratio"], 85.0)
        self.assertEqual(result["level"], na.FAULT)

    def test_assess_slot_no_data_disabled(self):
        result = na.assess_slot({"ACK": 5}, [])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["level"], na.HEALTHY)
        self.assertEqual(result["reason"], "no_slot_config")

    def test_assess_slot_multi_sample_mode(self):
        # 多帧时隙配置：CSMA/信标周期取众数作代表，占比取均值
        healthy = na.extract_slot_fields(self.SLOT_DETAIL)
        bad = na.extract_slot_fields(
            "|时隙配置[信标周期2000ms 信标时隙长度16ms "
            "RF信标时隙长度16ms CSMA时隙大小1700ms]|"
        )
        result = na.assess_slot({}, [healthy, healthy, bad])
        # 均值占比 (23.9% + 23.9% + 85%) / 3 ≈ 44.6% < 60% → 健康
        self.assertAlmostEqual(result["csma_ratio"], (23.88 * 2 + 85.0) / 3, delta=0.1)
        self.assertEqual(result["csma_slot_ms"], 500)  # 众数
        self.assertEqual(result["beacon_period_ms"], 2094)  # 众数
        self.assertEqual(result["level"], na.HEALTHY)

    def test_extract_route_fields(self):
        detail = "|路由评估剩余时间:50s| |经PCO通信成功数:81|"
        fields = na.extract_route_fields(detail)
        self.assertEqual(fields["route_estimate_s"], 50)
        self.assertEqual(fields["pco_success_count"], 81)

    def test_extract_route_fields_none(self):
        self.assertIsNone(na.extract_route_fields("|组网seq:238|"))
        self.assertIsNone(na.extract_route_fields(None))

    def test_extract_channel_fields(self):
        # 三种实测/近似格式都能识别，无相关内容返回 None
        self.assertEqual(
            na.extract_channel_fields("|信道变更[信道:1 剩余:30s]|"),
            {"channel": 1, "remain_s": 30},
        )
        self.assertEqual(
            na.extract_channel_fields("|无线信道变更[信道:2 剩余:20s]|"),
            {"channel": 2, "remain_s": 20},
        )
        self.assertEqual(
            na.extract_channel_fields("|信道切换剩余时间:15s|"),
            {"channel": None, "remain_s": 15},
        )
        self.assertIsNone(na.extract_channel_fields("|组网seq:238|"))

    def test_assess_route_channel_route_low_degraded(self):
        # 路由评估剩余 20s < 30s → 降级
        routes = [{"route_estimate_s": 20, "pco_success_count": 81}]
        result = na.assess_route_channel({}, routes, [])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["route_estimate_s"], 20)
        self.assertEqual(result["level"], na.DEGRADED)

    def test_assess_route_channel_many_channel_changes_degraded(self):
        # 信道变更 11 次 > 10 → 降级
        channels = [{"channel": 1, "remain_s": 30}] * 11
        result = na.assess_route_channel({}, [], channels)
        self.assertEqual(result["channel_change_count"], 11)
        self.assertEqual(result["level"], na.DEGRADED)

    def test_assess_route_channel_healthy(self):
        routes = [{"route_estimate_s": 50, "pco_success_count": 81}]
        result = na.assess_route_channel({}, routes, [])
        self.assertEqual(result["level"], na.HEALTHY)
        self.assertIsNone(result["reason"])

    def test_assess_route_channel_takes_min_remaining(self):
        # 多条路由字段取最紧张（最小）剩余时间
        routes = [
            {"route_estimate_s": 120, "pco_success_count": 81},
            {"route_estimate_s": 20, "pco_success_count": 5},
        ]
        result = na.assess_route_channel({}, routes, [])
        self.assertEqual(result["route_estimate_s"], 20)
        self.assertEqual(result["level"], na.DEGRADED)

    def test_assess_route_channel_no_data_disabled(self):
        result = na.assess_route_channel({}, [], [])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["level"], na.HEALTHY)
        self.assertEqual(result["reason"], "no_route_channel_data")

    def test_merge_slot_fault_dominates_cycle(self):
        # 成功率健康 + 稳定性健康 + 时隙故障 → 周期故障（最差合并）
        records = [make_record(1_000 + i * 100, f"STA{i % 5}") for i in range(20)]
        cycles = na.assess_periods(
            records, 3_000, {f"STA{i}" for i in range(5)},
            stability_level=na.HEALTHY,
            slot_level=na.FAULT,
            route_channel_level=na.HEALTHY,
        )
        self.assertEqual(cycles[0]["success_rate"], 100.0)
        self.assertEqual(cycles[0]["level"], na.FAULT)
        self.assertIn("时隙占用：fault", cycles[0]["level_reason"])
        self.assertIn("slot_level", cycles[0])
        self.assertIn("route_channel_level", cycles[0])

    def test_assess_by_network_exposes_slot_and_route_channel(self):
        # 网络级评估：summary.slot / summary.route_channel 透出，B 档可用
        base = 8 * 3600
        frame_dicts = []
        slot_detail = ("|时隙配置[信标周期2000ms 信标时隙长度16ms "
                       "RF信标时隙长度16ms CSMA时隙大小500ms]|")
        for i in range(10):
            t = f"{base // 3600:02d}:{(base % 3600) // 60:02d}:{base % 60:02d}.000"
            frame_dicts.append({
                "log_time": t,
                "raw_hex": build_central_beacon(cnt=i),
                "summary_json": json.dumps(
                    {"FrmType": "广播信标", "Detail": slot_detail}
                ),
            })
            base += 3  # 3 秒一跳，保证信标周期可识别
        records = [make_record(8 * 3600_000 + i * 3_000, f"STA{i}", nid=0x947F69)
                   for i in range(12)]
        result = na.assess_by_network(frame_dicts, records)
        network = result["networks"][0]
        self.assertIn("slot", network["summary"])
        self.assertIn("route_channel", network["summary"])
        self.assertTrue(network["summary"]["slot"]["enabled"])
        self.assertEqual(network["summary"]["slot"]["csma_slot_ms"], 500)
        self.assertEqual(network["summary"]["slot"]["beacon_period_ms"], 2000)
        # 帧里无路由/信道字段 → C 档 disabled
        self.assertFalse(network["summary"]["route_channel"]["enabled"])


class StreamPipelineTests(unittest.TestCase):
    """单趟流式管线：unassigned 透出、权威时长门禁、summary SNID 优先。"""

    def test_unresolvable_frames_not_mixed_into_networks(self):
        """NID 无法识别的帧不归属任何网络，单独计入 unassigned。"""
        frames = [
            {"log_time": "08:00:00.000", "raw_hex": build_central_beacon(nid=0x947F69, cnt=0)},
            {"log_time": "08:00:03.000", "raw_hex": "7E 00 7E"},  # 解析失败的帧
            {"log_time": "08:00:04.000", "raw_hex": build_gw_frame(nid=0x947F69)},
        ]
        result = na.assess_by_network_stream(frames)
        self.assertEqual(len(result["networks"]), 1)
        self.assertEqual(result["networks"][0]["frame_count"], 2)
        self.assertEqual(result["unassigned_frame_count"], 1)
        self.assertEqual(result["frame_total"], 3)

    def test_session_duration_enables_stability_gate(self):
        """session_duration_s 权威时长优先于帧跨度：短帧流 + 长会话 → 启用。"""
        frames = []
        for i in range(8):
            frames.append({
                "log_time": f"08:0{i}:00.000",
                "raw_hex": build_gw_frame(nid=0x947F69),
                "summary_json": json.dumps({"FrmType": "代理变更请求", "SNID": "00947F69"}),
            })
        fallback = na.assess_by_network_stream(iter(frames))
        self.assertEqual(fallback["networks"][0]["summary"]["stability"]["enabled"], False)
        self.assertEqual(fallback["networks"][0]["summary"]["stability"]["reason"],
                         "log_too_short")

        forced = na.assess_by_network_stream(
            iter(frames), session_duration_s=3 * 3600
        )
        stability = forced["networks"][0]["summary"]["stability"]
        self.assertEqual(stability["enabled"], True)
        self.assertEqual(forced["session_duration_s"], 3 * 3600)
        # 启用后按占比判级：全部为代理变更请求帧应降级
        self.assertEqual(stability["level"], na.DEGRADED)

    def test_summary_snid_takes_priority_over_fch(self):
        """NID 解析以 summary 的 SNID（DLL 权威）优先，缺失时 FCH 兜底。"""
        # SNID 与 FCH 不一致时按 SNID 归网
        frames = [{
            "log_time": "08:00:00.000",
            "raw_hex": build_gw_frame(nid=0x111111),
            "summary_json": json.dumps({"FrmType": "ACK", "SNID": "00947F69"}),
        }]
        result = na.assess_by_network_stream(frames)
        self.assertEqual([n["nid"] for n in result["networks"]], [0x947F69])
        # 无 summary 时 FCH 兜底
        frames = [{"log_time": "08:00:00.000", "raw_hex": build_gw_frame(nid=0x222222)}]
        result = na.assess_by_network_stream(frames)
        self.assertEqual([n["nid"] for n in result["networks"]], [0x222222])

    def test_nid_filter_excludes_frames_of_other_networks(self):
        """nid_filter 只统计指定网络，被排除帧计入 filtered_frame_count。"""
        frames = [
            {"log_time": "08:00:00.000", "raw_hex": build_gw_frame(nid=0x000456),
             "summary_json": json.dumps({"FrmType": "ACK", "SNID": "000456"})},
            {"log_time": "08:00:01.000", "raw_hex": build_gw_frame(nid=0x947F69),
             "summary_json": json.dumps({"FrmType": "ACK", "SNID": "00947F69"})},
        ]
        result = na.assess_by_network_stream(iter(frames), nid_filter=0x947F69)
        self.assertEqual([n["nid"] for n in result["networks"]], [0x947F69])
        self.assertEqual(result["filtered_frame_count"], 1)

    def test_aggregate_entry_matches_stream_entry(self):
        """SQL 物化聚合入口与流式入口在同源数据上产出一致。"""
        import itertools
        stream_frames = []
        for i in range(12):
            seconds = 8 * 3600 + i * 3
            t = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}.000"
            stream_frames.append({
                "log_time": t,
                "raw_hex": build_central_beacon(nid=0x947F69, cnt=i),
                "summary_json": json.dumps({
                    "FrmType": "中央信标", "SNID": "00947F69",
                    "Detail": "时隙分配[信标周期3000ms 信标时隙长度16ms "
                              "RF信标时隙长度16ms CSMA时隙大小500ms]",
                }),
            })
        for i in range(30):
            seconds = 8 * 3600 + 2 + i
            t = f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}.000"
            stream_frames.append({
                "log_time": t,
                "raw_hex": build_gw_frame(nid=0x947F69),
                "summary_json": json.dumps({"FrmType": "ACK", "SNID": "00947F69"}),
            })

        stream_result = na.assess_by_network_stream(
            iter(stream_frames), session_duration_s=7200.0
        )

        counts = {}
        for f in stream_frames:
            data = json.loads(f["summary_json"])
            counts[data["FrmType"]] = counts.get(data["FrmType"], 0) + 1
        counts_rows = [(0x947F69, frm, cnt) for frm, cnt in counts.items()]
        # 按 SQL loader 行形状：物化列 nid/frm_type 随行提供
        detail_rows = [
            {"log_time": f["log_time"], "nid": 0x947F69, "frm_type": "中央信标",
             "raw_hex": f["raw_hex"], "summary_json": f["summary_json"]}
            for f in stream_frames
            if "Detail" in json.loads(f["summary_json"])
        ]
        central_rows = [
            {"log_time": f["log_time"], "raw_hex": f["raw_hex"]}
            for f in stream_frames
            if "Detail" in json.loads(f["summary_json"])
        ]

        def bucket_rows(period):
            # 与流式分桶同规则：绝对毫秒取模分桶（数据在同一天内）
            agg = {}
            for f in stream_frames:
                clock = na._clock_ms(f["log_time"])
                bucket = clock - (clock % period)
                frm = json.loads(f["summary_json"])["FrmType"]
                key = (frm, bucket)
                agg[key] = agg.get(key, 0) + 1
            return [(0x947F69, frm, bucket, cnt) for (frm, bucket), cnt in agg.items()]

        agg_result = na.assess_by_network_aggregate(
            counts_rows, detail_rows, central_rows=central_rows,
            bucket_rows_fn=bucket_rows, session_duration_s=7200.0,
            frame_total=len(stream_frames), unassigned_total=0,
        )

        def snapshot(data):
            net = data["networks"][0]
            return {
                "period": net["beacon_period_ms"],
                "method": net["scan_method"],
                "mac": net["cco_mac"],
                "frames": net["frame_count"],
                "cycles": len(net["cycles"]),
                "overall": net["summary"]["overall_health"],
                "cycle_frames": sum(c["frame_count"] for c in net["cycles"]),
            }

        self.assertEqual(snapshot(stream_result), snapshot(agg_result))


if __name__ == "__main__":
    unittest.main()
