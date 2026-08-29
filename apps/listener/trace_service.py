"""侦听台通信流追踪服务（需求 0009）。

单引擎双模式：回放（对既有索引执行配对查询，输出完整报告）与 live（注册特征、
帧入库钩子增量匹配）共用同一特征 JSON、状态机与输出 schema（DESIGN §7.3）。

核心模型（DESIGN §4，G1 复核修正见 §10.1）：
- flow  = (业务ID, 报文序号)：协议自带配对键，上行序号回填下行序号；
- round = 时间簇：按空闲间隔切簇（缺省 60s 可配，§10 定稿值），簇内多条 flow，
  重传判定与 S3 反证均限定簇内；
- campaign = 业务ID × 时间窗内多轮聚合。
- 对账单位 = 应用层表地址（下行载荷目标序列 vs 上行应答），TEI 仅观测属性；
- 状态机 armed → sent(S1) → acked(S2a) → responded(S2b) → confirmed(S3)，
  断点 = 最后到达阶段；每阶段挂帧级证据（frame_id 钻取复用 frames/{id}）。

判定全部来自侦听台空口自证：S2a=链路 ACK（ack_peer==via_tei，§10.1）、
S2b=同序号上行、S3=0x0020 显式（铁证）或簇内响应后无重传（推断）；
坏帧不计入判定、单独计数（§9 口径）。
"""
import json
import uuid
from dataclasses import dataclass, field

from listener import network_assessment
from listener.log_service import TRACE_APP_IDS
from parser_lib.adapters.adapter_dualmode.trace_extract import extract_trace_fields

# 缺省空闲切簇间隔（秒）。DESIGN §4.3 观测闭合 T=10s 与 §10 校准 60s 并存，
# 取 §10 定稿值；response_policy.cluster_gap_seconds 可配置。
DEFAULT_CLUSTER_GAP_S = 60
# ACK/确认帧回捞的 id 上界余量：帧按 id 即时序插入，ACK 紧随被确认帧到达。
_ACK_ID_MARGIN = 1000

_SCOPES = ("flow", "round", "campaign")
_WINDOW_MODES = ("live", "time_range", "cursor_range")


class FeatureError(ValueError):
    """特征 JSON 校验失败。"""


@dataclass
class NormalizedFeature:
    scope: str
    app_id: str
    msg_seq: int | None
    frm_type: str | None
    dst_tei: str | None
    app_raw_contains: str | None
    nid: int | None
    channel: str | None
    start_time: str
    end_time: str
    start_id: int | None
    end_id: int | None
    window_mode: str
    timeout_ms: int | None
    use_ack_evidence: bool
    confirm_via_0x0020: bool
    expect_meters: list = field(default_factory=list)
    cluster_gap_s: int = DEFAULT_CLUSTER_GAP_S
    raw: dict = field(default_factory=dict)


def _parse_hex_int(value, name, bits=16, required=False):
    """十六进制优先解析（0x 前缀可选）；含非 hex 字符时按十进制兜底。"""
    text = str(value or "").strip()
    if not text:
        if required:
            raise FeatureError(f"{name} 必填")
        return None
    try:
        parsed = int(text, 16)
    except ValueError:
        try:
            parsed = int(text)
        except ValueError:
            raise FeatureError(f"{name} 格式无效：{value}") from None
    if not 0 <= parsed < (1 << bits):
        raise FeatureError(f"{name} 超出 {bits}bit 范围：{value}")
    return parsed


def validate_feature(feature: dict) -> NormalizedFeature:
    """校验并规范化特征 JSON（DESIGN §5.2）；失败抛 FeatureError。"""
    if not isinstance(feature, dict):
        raise FeatureError("特征必须是 JSON 对象")
    scope = feature.get("scope") or "round"
    if scope not in _SCOPES:
        raise FeatureError(f"scope 必须是 {'/'.join(_SCOPES)}：{scope}")
    feat = feature.get("feature") or {}
    if not isinstance(feat, dict):
        raise FeatureError("feature 必须是 JSON 对象")

    app_id = str(feat.get("app_id") or "").strip().upper()
    if not app_id:
        raise FeatureError("feature.app_id 必填（如 0003）")
    msg_seq = _parse_hex_int(feat.get("msg_seq"), "feature.msg_seq")
    if scope == "flow" and msg_seq is None:
        raise FeatureError("flow 粒度必须提供 feature.msg_seq（配对键）")
    if msg_seq is None and app_id not in TRACE_APP_IDS:
        raise FeatureError(
            f"feature.app_id 不在追踪范围（{'/'.join(sorted(TRACE_APP_IDS))}）：{app_id}"
        )

    window = feature.get("window") or {}
    if not isinstance(window, dict):
        raise FeatureError("window 必须是 JSON 对象")
    mode = window.get("mode") or "time_range"
    if mode not in _WINDOW_MODES:
        raise FeatureError(f"window.mode 必须是 {'/'.join(_WINDOW_MODES)}：{mode}")
    start_time = str(window.get("start_time") or "")
    end_time = str(window.get("end_time") or "")
    if mode == "time_range" and start_time and end_time and start_time > end_time:
        raise FeatureError("window.end_time 不能早于 start_time")
    start_id = window.get("start_id")
    end_id = window.get("end_id")
    for name, value in (("start_id", start_id), ("end_id", end_id)):
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise FeatureError(f"window.{name} 必须是非负整数")
    if start_id is not None and end_id is not None and start_id > end_id:
        raise FeatureError("window.end_id 不能小于 start_id")

    policy = feature.get("response_policy") or {}
    if not isinstance(policy, dict):
        raise FeatureError("response_policy 必须是 JSON 对象")
    timeout_ms = policy.get("timeout_ms")
    if timeout_ms is not None and (
        not isinstance(timeout_ms, (int, float)) or isinstance(timeout_ms, bool) or timeout_ms <= 0
    ):
        raise FeatureError("response_policy.timeout_ms 必须为正数")
    expect_meters = sorted({
        str(a).strip().upper() for a in (policy.get("expect_meters") or []) if str(a).strip()
    })
    cluster_gap_s = policy.get("cluster_gap_seconds") or DEFAULT_CLUSTER_GAP_S
    if not isinstance(cluster_gap_s, (int, float)) or isinstance(cluster_gap_s, bool) or cluster_gap_s <= 0:
        raise FeatureError("response_policy.cluster_gap_seconds 必须为正数")

    return NormalizedFeature(
        scope=scope,
        app_id=app_id,
        msg_seq=msg_seq,
        frm_type=str(feat.get("frm_type") or "").strip() or None,
        dst_tei=str(feat.get("dst_tei") or "").strip().upper() or None,
        app_raw_contains=str(feat.get("app_raw_contains") or "").strip().upper() or None,
        nid=_parse_hex_int(feat.get("nid"), "feature.nid", bits=24),
        channel=str(feat.get("channel") or "").strip() or None,
        start_time=start_time, end_time=end_time,
        start_id=start_id, end_id=end_id, window_mode=mode,
        timeout_ms=int(timeout_ms) if timeout_ms is not None else None,
        use_ack_evidence=bool(policy.get("use_ack_evidence", True)),
        confirm_via_0x0020=bool(policy.get("confirm_via_0x0020", True)),
        expect_meters=expect_meters,
        cluster_gap_s=int(cluster_gap_s),
        raw=feature,
    )


@dataclass
class _Frame:
    """配对查询装载的最小帧视图。"""

    frame_id: int
    log_time: str
    abs_ms: int | None
    flow_dir: int | None
    msg_seq: int | None
    sta_tei: str | None
    ori_tei: str | None
    meter_addrs: list | None
    ack_peer: str | None
    parse_error: str | None
    summary: dict = field(default_factory=dict)
    confirm: bool | None = None  # 0x0020 确认位（True 确认 / False 否认）


class TraceService:
    """通信流追踪引擎（侦听台侧）。

    回放模式对既有索引执行配对查询；live 模式由帧入库钩子喂入增量帧，
    两者共用 _cluster / _build_flows / _flow_report / _round_report。
    """

    _ROW_SQL = (
        "SELECT id, sequence, log_time, summary_json, parse_error, app_port, app_id, "
        "msg_seq, flow_dir, meter_addrs, sta_tei, ori_tei, ack_peer, frm_type FROM frames"
    )

    def __init__(self, log_service):
        self.log_service = log_service

    # ------------------------------------------------------------------
    # 回放入口
    # ------------------------------------------------------------------

    def run_replay(self, feature: dict, index_id=None) -> dict:
        nf = validate_feature(feature)
        service = self.log_service.open_index(index_id) if index_id else self.log_service
        if not service.trace_ready():
            # 物化列未就绪：触发后台回填；本次行源以 summary_json 兜底（正确性不变）
            service.request_backfill()

        rows, bad_frames = self._load_frames(service, nf)
        if not rows:
            return self._empty_report(nf, bad_frames)
        self._stitch_abs_time(rows)

        acks, confirms = self._load_evidence(service, nf, rows)
        clusters = self._cluster(rows, nf.cluster_gap_s * 1000)

        rounds = []
        flow_reports = []
        for index, cluster in enumerate(clusters):
            flows = self._build_flows(cluster, nf.app_id)
            reports = [self._flow_report(f, acks, confirms, nf) for f in flows]
            flow_reports.extend(reports)
            rounds.append(self._round_report(index, cluster, reports, nf, bad_frames))

        report = {
            "trace_id": "tr-" + uuid.uuid4().hex[:12],
            "mode": "replay",
            "scope": nf.scope,
            "feature": nf.raw,
            "summary": self._summary(rounds, bad_frames),
            "rounds": rounds,
            "proxy_graph": self._proxy_graph(flow_reports),
            "artifact_id": None,
        }
        if nf.scope == "flow":
            report["flow"] = flow_reports[0] if flow_reports else None
        return report

    # ------------------------------------------------------------------
    # 行源与聚类
    # ------------------------------------------------------------------

    def _load_frames(self, service, nf: NormalizedFeature) -> tuple[list[_Frame], int]:
        """装载特征匹配帧（双向）+ 窗口内坏帧计数。物化列未就绪时 LIKE 收窄。"""
        conditions = ["app_id = ?"]
        parameters: list = [nf.app_id]
        if nf.msg_seq is not None:
            conditions.append("(msg_seq = ? OR msg_seq IS NULL)")
            parameters.append(nf.msg_seq)
        if nf.dst_tei:
            conditions.append("sta_tei = ?")
            parameters.append(nf.dst_tei)
        if nf.nid is not None:
            conditions.append("nid = ?")
            parameters.append(nf.nid)
        time_bounded = False
        if nf.window_mode == "time_range":
            service._append_time_range(conditions, parameters, nf.start_time, nf.end_time)
            time_bounded = True
        elif nf.window_mode == "cursor_range":
            if nf.start_id is not None:
                conditions.append("id >= ?")
                parameters.append(nf.start_id)
            if nf.end_id is not None:
                conditions.append("id <= ?")
                parameters.append(nf.end_id)
        # live 模式回放视图：不设窗口（与注册表的增量句柄互补）

        ready = service.trace_ready()
        if not ready:
            # 兜底：存量库物化列可能整列为 NULL，用 summary LIKE 收窄候选集
            conditions.append(
                f"(app_id IS NOT NULL OR summary_json LIKE ?)"
            )
            parameters.append(f'%"APP_ID": "{nf.app_id}"%')

        with service._connect() as connection:
            rows = connection.execute(
                self._ROW_SQL + f" WHERE {' AND '.join(conditions)} ORDER BY id",
                parameters,
            ).fetchall()
            bad_frames = self._count_bad_frames(service, nf, time_bounded)

        frames = []
        for row in rows:
            frame = self._to_frame(row, ready)
            if frame is not None:
                frames.append(frame)
        frames = [f for f in self._apply_l2_filters(frames, nf)]
        if nf.msg_seq is not None:
            # 未物化库兜底路径会带出其他序号行，按配对键收口
            frames = [f for f in frames if f.msg_seq == nf.msg_seq]
        return frames, bad_frames

    def _count_bad_frames(self, service, nf: NormalizedFeature, time_bounded: bool) -> int:
        """窗口内 CRC 坏帧计数（口径：不计入判定、单独计数）。"""
        conditions = ["parse_error IS NOT NULL"]
        parameters: list = []
        if nf.nid is not None:
            conditions.append("nid = ?")
            parameters.append(nf.nid)
        if nf.window_mode == "time_range" or time_bounded:
            service._append_time_range(conditions, parameters, nf.start_time, nf.end_time)
        elif nf.window_mode == "cursor_range":
            if nf.start_id is not None:
                conditions.append("id >= ?")
                parameters.append(nf.start_id)
            if nf.end_id is not None:
                conditions.append("id <= ?")
                parameters.append(nf.end_id)
        with service._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM frames WHERE {' AND '.join(conditions)}", parameters
            ).fetchone()
        return row[0] or 0

    def _to_frame(self, row, ready: bool) -> _Frame | None:
        try:
            meter_addrs = json.loads(row["meter_addrs"]) if row["meter_addrs"] else None
        except (TypeError, ValueError):
            meter_addrs = None
        summary = {}
        if row["summary_json"]:
            try:
                summary = json.loads(row["summary_json"])
            except (TypeError, ValueError):
                summary = {}
        app_id = (row["app_id"] or (summary.get("APP_ID") or "").upper() or None)
        if not app_id and not row["ack_peer"]:
            # ACK 行无 app_id（ack_peer 即其追踪标识），其余无应用层帧丢弃
            return None
        msg_seq = row["msg_seq"]
        confirm: bool | None = None
        if not ready:
            # 未物化库兜底：从 summary 重新提取
            src = summary.get("SRC")
            if (summary.get("APP_RAW") or "") and app_id.upper() in TRACE_APP_IDS:
                try:
                    ext = extract_trace_fields(
                        app_id.upper(), bytes.fromhex(summary["APP_RAW"]), src
                    )
                except (ValueError, TypeError):
                    ext = None
                if ext is not None:
                    msg_seq = ext.msg_seq
                    if ext.direction == "up":
                        meter_addrs = ext.responses
                    else:
                        meter_addrs = ext.targets or meter_addrs
                    confirm = ext.confirm
        elif (app_id or "").upper() == "0020" and (summary.get("APP_RAW") or ""):
            # 已物化库：确认位未单独成列，按需从 APP_RAW 提取（0020 帧量小）
            try:
                ext20 = extract_trace_fields(
                    "0020", bytes.fromhex(summary["APP_RAW"]), summary.get("SRC")
                )
            except (ValueError, TypeError):
                ext20 = None
            if ext20 is not None:
                confirm = ext20.confirm
        return _Frame(
            frame_id=row["id"],
            log_time=row["log_time"],
            abs_ms=None,
            flow_dir=row["flow_dir"],
            msg_seq=msg_seq,
            sta_tei=row["sta_tei"],
            ori_tei=row["ori_tei"],
            meter_addrs=meter_addrs,
            ack_peer=row["ack_peer"],
            parse_error=row["parse_error"],
            summary=summary,
            confirm=confirm,
        )

    @staticmethod
    def _stitch_abs_time(frames: list[_Frame]) -> None:
        """按 id 序（即时间序）补绝对毫秒，处理跨天翻转。"""
        abs_list = network_assessment._absolute_ms([f.log_time for f in frames])
        for frame, abs_ms in zip(frames, abs_list):
            frame.abs_ms = abs_ms

    @staticmethod
    def _apply_l2_filters(frames: list[_Frame], nf: NormalizedFeature) -> list[_Frame]:
        """L2 过滤：frm_type / channel / app_raw_contains（物化列未覆盖项）。"""
        out = []
        for frame in frames:
            if nf.frm_type and frame.summary.get("FrmType") != nf.frm_type:
                continue
            if nf.channel and frame.summary.get("ChType") != nf.channel:
                continue
            if nf.app_raw_contains:
                app_raw = str(frame.summary.get("APP_RAW") or "").upper()
                if nf.app_raw_contains not in app_raw:
                    continue
            out.append(frame)
        return out

    @staticmethod
    def _cluster(frames: list[_Frame], gap_ms: int) -> list[list[_Frame]]:
        """空闲间隔切簇（round 的实现载体，§4.1/§10.1）。"""
        ordered = sorted(
            (f for f in frames if f.abs_ms is not None),
            key=lambda f: (f.abs_ms, f.frame_id),
        )
        clusters: list[list[_Frame]] = []
        for frame in ordered:
            if clusters and frame.abs_ms - clusters[-1][-1].abs_ms <= gap_ms:
                clusters[-1].append(frame)
            else:
                clusters.append([frame])
        for frame in frames:
            if frame.abs_ms is None:
                clusters.append([frame])
        return clusters

    # ------------------------------------------------------------------
    # 证据回捞（ACK / 0x0020）
    # ------------------------------------------------------------------

    def _load_evidence(self, service, nf: NormalizedFeature, rows: list[_Frame]):
        """回捞 ACK（ack_peer 命中特征对端 TEI）与 0x0020 确认帧。"""
        sta_teis = sorted({f.sta_tei for f in rows if f.sta_tei})
        seqs = sorted({f.msg_seq for f in rows if f.msg_seq is not None})
        acks: dict[int, _Frame] = {}
        confirms: dict[int, list[_Frame]] = {}
        if not rows:
            return acks, confirms

        min_id = min(f.frame_id for f in rows)
        max_id = max(f.frame_id for f in rows)

        if nf.use_ack_evidence and sta_teis:
            conditions = ["ack_peer IS NOT NULL", f"ack_peer IN ({', '.join('?' for _ in sta_teis)})"]
            parameters: list = list(sta_teis)
            if nf.window_mode == "time_range" and nf.end_time:
                conditions.append("log_time <= ?")
                parameters.append(service._time_range_bound(nf.end_time, is_end=True))
            elif nf.window_mode == "cursor_range":
                if nf.start_id is not None:
                    conditions.append("id >= ?")
                    parameters.append(nf.start_id)
                upper = (nf.end_id if nf.end_id is not None else max_id) + _ACK_ID_MARGIN
                conditions.append("id <= ?")
                parameters.append(upper)
            else:
                conditions.append("id <= ?")
                parameters.append(max_id + _ACK_ID_MARGIN)
            if nf.nid is not None:
                conditions.append("nid = ?")
                parameters.append(nf.nid)
            with service._connect() as connection:
                for row in connection.execute(
                    self._ROW_SQL + f" WHERE {' AND '.join(conditions)} ORDER BY id",
                    parameters,
                ):
                    frame = self._to_frame(row, True)
                    if frame is not None:
                        acks[frame.frame_id] = frame

        if nf.confirm_via_0x0020 and seqs:
            conditions = ["app_id = '0020'", "flow_dir = 0"]
            parameters = []
            if nf.msg_seq is not None:
                conditions.append("msg_seq = ?")
                parameters.append(nf.msg_seq)
            else:
                conditions.append(f"msg_seq IN ({', '.join('?' for _ in seqs)})")
                parameters.extend(seqs)
            if nf.window_mode == "time_range" and nf.end_time:
                conditions.append("log_time <= ?")
                parameters.append(service._time_range_bound(nf.end_time, is_end=True))
            elif nf.window_mode == "cursor_range":
                if nf.start_id is not None:
                    conditions.append("id >= ?")
                    parameters.append(nf.start_id)
                upper = (nf.end_id if nf.end_id is not None else max_id) + _ACK_ID_MARGIN
                conditions.append("id <= ?")
                parameters.append(upper)
            else:
                conditions.append("id <= ?")
                parameters.append(max_id + _ACK_ID_MARGIN)
            if nf.nid is not None:
                conditions.append("nid = ?")
                parameters.append(nf.nid)
            with service._connect() as connection:
                for row in connection.execute(
                    self._ROW_SQL + f" WHERE {' AND '.join(conditions)} ORDER BY id",
                    parameters,
                ):
                    frame = self._to_frame(row, True)
                    if frame is None or frame.msg_seq is None:
                        continue
                    confirms.setdefault(frame.msg_seq, []).append(frame)
        self._stitch_abs_time(list(acks.values()) + [c for v in confirms.values() for c in v])
        return acks, confirms

    # ------------------------------------------------------------------
    # 流构建与状态机
    # ------------------------------------------------------------------

    @staticmethod
    def _build_flows(cluster: list[_Frame], app_id: str) -> list[dict]:
        """簇内按 (msg_seq) 组流；组内保序。"""
        grouped: dict[int, dict] = {}
        for frame in sorted(cluster, key=lambda f: f.frame_id):
            if frame.msg_seq is None:
                continue
            flow = grouped.setdefault(
                frame.msg_seq,
                {"app_id": app_id, "msg_seq": frame.msg_seq, "downs": [], "ups": []},
            )
            if frame.flow_dir == 0:
                flow["downs"].append(frame)
            else:
                flow["ups"].append(frame)
        return sorted(grouped.values(), key=lambda f: min(x.frame_id for x in f["downs"] + f["ups"]))

    def _flow_report(self, flow: dict, acks: dict, confirms: dict, nf: NormalizedFeature) -> dict:
        """单流状态机：armed→sent→acked→responded→confirmed/denied/timeout。"""
        downs = sorted(flow["downs"], key=lambda f: f.frame_id)
        ups = sorted(flow["ups"], key=lambda f: f.frame_id)
        seq = flow["msg_seq"]
        # via_tei 优先取非广播对端：同序号流内单播与广播中继副本并存（§3 实证）
        via_tei = next(
            (d.sta_tei for d in downs if d.sta_tei and d.sta_tei != "FFF"),
            (downs[0].sta_tei if downs else (ups[0].sta_tei if ups else None)),
        )

        sent = None
        retransmissions = []
        if downs:
            first = downs[0]
            sent = {"frame_id": first.frame_id, "t": first.log_time, "retries": len(downs) - 1}
            prev = first
            for later in downs[1:]:
                interval = (
                    later.abs_ms - prev.abs_ms
                    if later.abs_ms is not None and prev.abs_ms is not None else None
                )
                retransmissions.append(
                    {"frame_id": later.frame_id, "t": later.log_time, "interval_ms": interval}
                )
                prev = later

        ack = None
        if nf.use_ack_evidence and via_tei and downs:
            ack = self._attribute_ack(downs, via_tei, acks)

        response = None
        denied_addrs: list[str] = []
        if ups:
            first_up = ups[0]
            latency = (
                first_up.abs_ms - downs[0].abs_ms
                if downs and first_up.abs_ms is not None and downs[0].abs_ms is not None
                else None
            )
            for up in ups:
                for item in up.meter_addrs or []:
                    if isinstance(item, dict) and item.get("denied") and item.get("addr"):
                        denied_addrs.append(item["addr"])
            response = {
                "frame_id": first_up.frame_id,
                "t": first_up.log_time,
                "latency_ms": latency,
                "responded_sta": first_up.ori_tei or first_up.sta_tei,
            }

        confirm = None
        if nf.confirm_via_0x0020:
            for cf in confirms.get(seq, []):
                if downs and cf.abs_ms is not None and downs[0].abs_ms is not None \
                        and cf.abs_ms < downs[0].abs_ms:
                    continue
                confirm = {"frame_id": cf.frame_id, "t": cf.log_time, "denied": cf.confirm is False}
                break

        stage, s3 = self._flow_stage(flow, downs, ups, ack, confirm, retransmissions, denied_addrs, nf)
        return {
            "flow_key": f"{flow['app_id']}:{seq:04X}",
            "app_id": flow["app_id"],
            "msg_seq": f"0x{seq:04X}",
            "via_tei": via_tei,
            "sent": sent,
            "retransmissions": retransmissions,
            "ack": ack,
            "response": response,
            "confirm": confirm,
            "stage": stage,
            "s3": s3,
            "targets": self._flow_targets(flow),
            "responses": self._flow_responses(flow),
        }

    @staticmethod
    def _attribute_ack(downs, via_tei, acks):
        """ACK 归属（§10.1）：ack_peer==via_tei，取任一下行之后最近的一条。"""
        candidates = [
            f for f in acks.values()
            if f.ack_peer == via_tei and f.frame_id > downs[0].frame_id
        ]
        if not candidates:
            return None
        nearest = min(candidates, key=lambda f: f.frame_id)
        return {"frame_id": nearest.frame_id, "t": nearest.log_time}

    @staticmethod
    def _flow_targets(flow: dict) -> list[str]:
        targets: list[str] = []
        for down in sorted(flow["downs"], key=lambda f: f.frame_id):
            for addr in down.meter_addrs or []:
                if isinstance(addr, str) and addr not in targets:
                    targets.append(addr)
        return targets

    @staticmethod
    def _flow_responses(flow: dict) -> list[dict]:
        responses: dict[str, dict] = {}
        for up in sorted(flow["ups"], key=lambda f: f.frame_id):
            for item in up.meter_addrs or []:
                if isinstance(item, str):
                    item = {"addr": item, "denied": False}
                addr = item.get("addr")
                if not addr:
                    continue
                prior = responses.get(addr)
                # 任一次应答正常即视为最终正常（重发期间先否认后正常）
                if prior is None or not item.get("denied"):
                    responses[addr] = {
                        "addr": addr, "denied": bool(item.get("denied")),
                        "frame_id": up.frame_id,
                    }
        return list(responses.values())

    @staticmethod
    def _retx_after_response(flow, retransmissions) -> bool:
        """存在发生在最后一条响应之后的重传（S3 反证失败，§4.4 retransmitted）。"""
        ups = flow["ups"]
        if not ups:
            return False
        last_up_id = max(f.frame_id for f in ups)
        return any(r["frame_id"] > last_up_id for r in retransmissions)

    def _flow_stage(self, flow, downs, ups, ack, confirm, retransmissions,
                    denied_addrs, nf: NormalizedFeature):
        """状态机判定，返回 (stage, s3)。断点 = 最后到达阶段（§4.4）。"""
        if downs and ups:
            s3_verdict, evidence_kind = "none", "none"
            if nf.confirm_via_0x0020 and confirm and not confirm.get("denied"):
                s3_verdict, evidence_kind = "confirmed", "explicit_ack"
            elif self._retx_after_response(flow, retransmissions):
                s3_verdict, evidence_kind = "not_confirmed", "retransmitted"
            else:
                s3_verdict, evidence_kind = "confirmed", "no_retransmit_inference"
            resp_addrs = {i["addr"] for i in self._flow_responses(flow)}
            if resp_addrs and resp_addrs <= set(denied_addrs):
                # 全部应答表均否认：否认是一等结果（DESIGN §4.2），单独分类
                return "denied", {"verdict": "denied", "evidence_kind": "explicit_ack"}
            stage = "confirmed" if s3_verdict == "confirmed" else "responded"
            return stage, {"verdict": s3_verdict, "evidence_kind": evidence_kind}
        if downs:
            if ack:
                return "acked", {"verdict": "none", "evidence_kind": "none"}
            return "sent", {"verdict": "none", "evidence_kind": "none"}
        if ups:
            # 上行发起流（如 0x0008 事件上报）：CCO 的 0x0020 即接收回执（§2 铁证）
            if confirm:
                if confirm.get("denied"):
                    return "denied", {"verdict": "denied", "evidence_kind": "explicit_ack"}
                return "confirmed", {"verdict": "confirmed", "evidence_kind": "explicit_ack"}
            return "responded", {"verdict": "none", "evidence_kind": "none"}
        return "armed", {"verdict": "none", "evidence_kind": "none"}

    # ------------------------------------------------------------------
    # 轮报告与汇总
    # ------------------------------------------------------------------

    def _round_report(self, index, cluster, flow_reports, nf, bad_frames) -> dict:
        targets: dict[str, str | None] = {}
        responses: dict[str, dict] = {}
        for report in flow_reports:
            for addr in report["targets"]:
                targets.setdefault(addr, report["via_tei"])
            for item in report["responses"]:
                prior = responses.get(item["addr"])
                if prior is None or not item["denied"]:
                    responses[item["addr"]] = item

        if nf.expect_meters:
            expected = set(nf.expect_meters)
            targets = {k: v for k, v in targets.items() if k in expected}

        meter_table = []
        for addr, via_tei in sorted(targets.items()):
            resp = responses.get(addr)
            status = "missing" if resp is None else ("denied" if resp["denied"] else "ok")
            meter_table.append({
                "meter_addr": addr, "via_tei": via_tei, "status": status,
                "flow_key": next(
                    (r["flow_key"] for r in flow_reports if addr in r["targets"]), None
                ),
            })
        # 应答了但不在本轮下行目标里的表（动态应答/档案外），单独列出不混入缺席统计
        for addr, resp in sorted(responses.items()):
            if addr not in targets:
                meter_table.append({
                    "meter_addr": addr, "via_tei": None,
                    "status": "denied" if resp["denied"] else "ok",
                    "flow_key": next(
                        (r["flow_key"] for r in flow_reports
                         if any(i["addr"] == addr for i in r["responses"])), None
                    ),
                    "extra": True,
                })

        start = cluster[0].log_time if cluster else ""
        end = cluster[-1].log_time if cluster else ""
        duration = (
            cluster[-1].abs_ms - cluster[0].abs_ms
            if cluster and cluster[0].abs_ms is not None and cluster[-1].abs_ms is not None
            else None
        )
        return {
            "round_id": f"rd-{index:04d}",
            "cluster_index": index,
            "start_t": start, "end_t": end, "duration_ms": duration,
            "msg_seqs": sorted({f["msg_seq"] for f in flow_reports}),
            "flows": flow_reports,
            "meter_table": meter_table,
            "meters": {
                "targets": len(targets),
                "responded": sum(1 for m in meter_table if m["status"] == "ok" and not m.get("extra")),
                "denied": sum(1 for m in meter_table if m["status"] == "denied"),
                "missing": sum(1 for m in meter_table if m["status"] == "missing"),
            },
            "bad_frames": bad_frames,
        }

    @staticmethod
    def _summary(rounds, bad_frames) -> dict:
        flow_reports = [f for r in rounds for f in r["flows"]]
        stages = [f["stage"] for f in flow_reports]
        return {
            "rounds": len(rounds),
            "meters": sum(r["meters"]["targets"] for r in rounds),
            "full_chain": sum(1 for s in stages if s == "confirmed"),
            "no_ack": sum(1 for s in stages if s == "sent"),
            "no_response": sum(1 for s in stages if s == "acked"),
            "denied": sum(1 for s in stages if s == "denied"),
            "no_confirm": sum(1 for s in stages if s == "responded"),
            "flows": len(flow_reports),
            "bad_frames": bad_frames,
        }

    @staticmethod
    def _proxy_graph(flow_reports) -> list[dict]:
        """多轮累积的 (表地址 → 应答 STA) 代理观测（DESIGN §4.2 副产物）。"""
        observations: dict[tuple, dict] = {}
        for report in flow_reports:
            sta = report["response"].get("responded_sta") if report["response"] else None
            for item in report["responses"]:
                key = (item["addr"], sta)
                entry = observations.setdefault(
                    key, {"meter_addr": item["addr"], "sta_tei": sta, "observations": 0}
                )
                entry["observations"] += 1
        return sorted(
            observations.values(),
            key=lambda e: (-e["observations"], e["meter_addr"]),
        )

    def _empty_report(self, nf: NormalizedFeature, bad_frames: int) -> dict:
        report = {
            "trace_id": "tr-" + uuid.uuid4().hex[:12],
            "mode": "replay",
            "scope": nf.scope,
            "feature": nf.raw,
            "summary": {
                "rounds": 0, "meters": 0, "full_chain": 0, "no_ack": 0,
                "no_response": 0, "denied": 0, "no_confirm": 0,
                "flows": 0, "bad_frames": bad_frames,
            },
            "rounds": [],
            "proxy_graph": [],
            "artifact_id": None,
        }
        if nf.scope == "flow":
            report["flow"] = None
        return report

    # ------------------------------------------------------------------
    # feature_hint（样例反推，DESIGN §5.3；页面 frames/{id} 调用）
    # ------------------------------------------------------------------

    def build_feature_hint(self, frame: dict) -> dict | None:
        """由单帧反推特征草稿；非应用层帧返回 None。"""
        summary = frame.get("summary") or {}
        app_id = str(summary.get("APP_ID") or "").upper()
        if not app_id or app_id not in TRACE_APP_IDS:
            return None
        src = summary.get("SRC")
        hint = {
            "scope": "round" if app_id in TRACE_APP_IDS else "flow",
            "feature": {
                "frm_type": summary.get("FrmType"),
                "app_id": app_id,
                "dst_tei": (summary.get("DST") or "") if src == "001" else "",
                "msg_seq": "", "app_raw_contains": "", "nid": "", "channel": "",
            },
            "response_policy": {},
            "tips": [],
        }
        app_raw = None
        if summary.get("APP_RAW"):
            try:
                app_raw = bytes.fromhex(summary["APP_RAW"])
            except (ValueError, TypeError):
                app_raw = None
        if app_raw is not None and app_id in TRACE_APP_IDS:
            ext = extract_trace_fields(app_id, app_raw, src)
            if ext is not None and ext.msg_seq is not None:
                hint["feature"]["msg_seq"] = f"{ext.msg_seq:04X}"
                hint["tips"].append(f"该帧报文序号 0x{ext.msg_seq:04X}；将 msg_seq 留空可升级为 campaign 聚合")
            if ext is not None and ext.direction == "down" and ext.targets:
                hint["response_policy"]["expect_meters"] = ext.targets
                hint["tips"].append(f"下行载荷解析出 {len(ext.targets)} 个目标表地址")
        if src != "001" and summary.get("SRC"):
            hint["tips"].append("该帧为上行帧；追踪其应答链请以对端下行帧为锚")
        return hint
