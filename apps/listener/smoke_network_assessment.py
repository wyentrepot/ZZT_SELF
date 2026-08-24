"""真实副本日志冒烟：索引 → 服务层评估 → API 层结构校验。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path[:0] = ["", "apps", "libs"]

from fastapi.testclient import TestClient

from listener.app import _build_parser_service, create_app
from listener.index_registry import ListenerIndexRegistry
from listener.log_service import LogFileService

LOG_FILE = Path("测试文件/模块快日志/侦听台 - 副本/COM4_20260812_162044_自动保存.txt")

tmp = tempfile.TemporaryDirectory()
registry = ListenerIndexRegistry(Path(tmp.name) / "indexes")
parser = _build_parser_service()
service = LogFileService(parser, Path(tmp.name) / "log_index.sqlite3",
                         index_registry=registry)

print("parser available:", parser is not None)
print("indexing", LOG_FILE, "...")
result = service.start_index(str(LOG_FILE.resolve()))
import time
while service.status()["state"] in ("queued", "indexing"):
    time.sleep(0.5)
status = service.status()
print("index status:", {k: status[k] for k in ("state", "frame_count", "error_count", "message")})

# 服务层评估
assessment = service.list_beacon_periods()
print("\n=== 服务层 list_beacon_periods ===")
print("networks:", len(assessment["networks"]), "beacon_period_ms:", assessment["beacon_period_ms"],
      "overall_health:", assessment["overall_health"])
for net in assessment["networks"][:3]:
    print("  nid=%s cco_mac=%s period=%s conf=%s method=%s cycles=%d frames=%d records=%d active=%d" % (
        net["nid"], net["cco_mac"], net["beacon_period_ms"], net["confidence"],
        net["scan_method"], len(net["cycles"]), net.get("frame_count"),
        net.get("record_count"), net.get("active_sta_count")))
    if net["cycles"]:
        c = net["cycles"][0]
        print("    first cycle:", json.dumps({k: c[k] for k in
              ("start_time", "end_time", "beacon_period_ms", "frame_count",
               "success_rate", "offline_rate", "active_sta_count", "rating",
               "level_reason")}, ensure_ascii=False))
    print("    summary:", json.dumps(net["summary"], ensure_ascii=False)[:300])

# 诊断：分钟上报采样
sample_records = service.sample_reports_for_assessment()
print("\n=== 诊断 sample_reports_for_assessment ===")
print("sampled records:", len(sample_records), "nids:", sorted({r["nid"] for r in sample_records})[:5],
      "stas:", sorted({r["station_key"] for r in sample_records})[:5])

# API 层
app = create_app(parser, service)
client = TestClient(app)

for path in ("/api/network/assessment", "/api/network/status"):
    resp = client.get(path)
    print("\n=== GET %s -> %d ===" % (path, resp.status_code))
    data = resp.json()
    if path.endswith("status"):
        print(json.dumps(data, ensure_ascii=False)[:800])
    else:
        print("keys:", sorted(data.keys()))
        print("beacon_period_ms:", data["beacon_period_ms"], "overall_health:", data["overall_health"],
              "fallback:", data.get("fallback"))
        if data["networks"]:
            net = data["networks"][0]
            print("net0:", {k: net[k] for k in ("nid", "cco_mac", "beacon_period_ms", "confidence")})
            if net["cycles"]:
                cyc = net["cycles"][0]
                print("cycle0 keys subset:", {k: cyc.get(k) for k in
                      ("start_time", "end_time", "beacon_period_ms", "frame_count",
                       "success_rate", "offline_rate", "active_sta_count", "rating")})

# 未启用 log_service 时 503
app_no_svc = create_app(parser, None)
no_svc_client = TestClient(app_no_svc)
resp = no_svc_client.get("/api/network/assessment")
print("\n=== log_service=None ->", resp.status_code, "===")
resp = no_svc_client.get("/api/network/status")
print("=== status log_service=None ->", resp.status_code, "===")

tmp.cleanup()
print("\nSMOKE DONE")
