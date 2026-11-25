# -*- coding: utf-8 -*-
"""wsl-control v2 单元测试（纯 WSL 可跑，不依赖 Windows）。

覆盖：配置合并、令牌、快照缓存/长轮询、端口归属判定、逐口开关（改配置+重启）、
服务动作、doctor、日志、审计、usbipd 410、非回环鉴权。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wsl_control", HERE / "wsl-control.py")
wc = importlib.util.module_from_spec(spec)
sys.modules["wsl_control"] = wc
spec.loader.exec_module(wc)


class FakeHost:
    is_windows = False

    def __init__(self):
        self.coms = [{"com": "COM12", "device": "CP210x"}, {"com": "COM18", "device": "CH340"}]
        self.files = {}
        self.tcp = {}
        self.http = {}
        self.runs = []
        self.spawned = []
        self.alive = set()
        self.pids = {}
        self.stat_info = {}

    # files
    def read_text(self, path):
        return self.files.get(path)

    def write_text(self, path, text):
        self.files[path] = text

    def append_text(self, path, text):
        self.files[path] = (self.files.get(path) or "") + text

    def stat(self, path):
        return self.stat_info.get(path)

    # net
    def serial_ports(self):
        return list(self.coms)

    def tcp_open(self, host, port, timeout=0.4):
        return bool(self.tcp.get(port))

    def http_json(self, url, timeout=1.5):
        for key, value in self.http.items():
            if key in url:
                return value
        return None

    # proc
    def proc_alive(self, pid):
        return pid in self.alive

    def spawn(self, args, cwd, env, log_path):
        pid = 1000 + len(self.spawned)
        self.spawned.append(list(args))
        self.alive.add(pid)
        return pid

    def terminate(self, pid):
        self.alive.discard(pid)
        return True

    def run(self, args, timeout=8.0):
        self.runs.append(list(args))
        if "schtasks" in args and "/Query" in args:
            return 0, "TaskName: FC-Diag-Service\r\nStatus: Running\r\n"
        return 0, "OK"

    def pid_listening(self, port):
        return self.pids.get(port)

    def pid_connected_to(self, ports):
        return None


class AgentForTest(wc.HostAgent):
    def __init__(self, settings, host, tmp: Path, clock=None):
        self._paths = {
            "config": str(tmp / "serial_bridge.json"),
            "status": str(tmp / "status.json"),
            "pid": str(tmp / "bridge.pid"),
            "log": str(tmp / "bridge.log"),
            "script": str(tmp / "serial_bridge.py"),
        }
        super().__init__(settings, host=host, token_path=str(tmp / "token"),
                         audit_path=str(tmp / "audit.jsonl"),
                         log_path=str(tmp / "service.log"),
                         clock=clock or time.time)

    def bridge_config(self):
        return self._paths["config"]

    def bridge_status_path(self):
        return self._paths["status"]

    def bridge_pid_file(self):
        return self._paths["pid"]

    def bridge_log(self):
        return self._paths["log"]

    def bridge_script(self):
        return self._paths["script"]


BASE_CONFIG = {
    "version": 1,
    "ports": [
        {"id": "cco-main", "windows_com": "COM12", "label": "CCO", "usage": "cco",
         "enabled": True, "baudrate": 115200, "parity": "N"},
        {"id": "sta-main", "windows_com": "COM18", "label": "STA", "usage": "sta",
         "enabled": True, "baudrate": 115200, "parity": "N"},
        {"id": "factory", "windows_com": "COM7", "label": "厂测", "usage": "",
         "enabled": False, "baudrate": 9600, "parity": "N"},
    ],
}


def make_agent(tmp: Path, clock=None, status_ports=None):
    host = FakeHost()
    settings = wc.deep_merge(wc.DEFAULT_SETTINGS, {
        "wsl": {"distro": "TestDistro", "repo": "/repo"},
        "bridge": {"start_timeout_seconds": 2},
        "gateway": {"port": 8700, "build_plain": str(tmp / "bp"),
                    "python": str(tmp / "python.exe")},
        "diag": {"task": "FC-Diag-Service", "port": 8765, "start_timeout_seconds": 2},
        "workbench": {"port": 8790, "health_path": "/api/health"},
    })
    agent = AgentForTest(settings, host, tmp, clock=clock)
    host.files[agent.bridge_config()] = json.dumps(BASE_CONFIG, ensure_ascii=False)
    host.files[agent.bridge_script()] = "# bridge"
    ports = status_ports if status_ports is not None else {
        "cco-main": {"id": "cco-main", "windows_com": "COM12", "client_connected": True,
                     "control_connected": True, "desired_baud": 115200,
                     "link": "/run/hplc/serial/cco-main", "pts": "/dev/pts/3",
                     "last_error": ""},
        "sta-main": {"id": "sta-main", "windows_com": "COM18", "client_connected": True,
                     "control_connected": True, "desired_baud": 115200,
                     "link": "/run/hplc/serial/sta-main", "pts": "/dev/pts/4",
                     "last_error": ""},
    }
    host.files[agent.bridge_status_path()] = json.dumps({
        "version": 1, "pid": 34898, "started_at": "2026-11-17T16:00:00",
        "updated_at": "2026-11-17T17:00:00", "ports": ports, "config_stale": False,
    }, ensure_ascii=False)
    host.files[agent.bridge_pid_file()] = "28260"
    host.alive.add(28260)
    host.http["8765/health"] = {"service": "fc-service-8765", "version": "0.3.1"}
    host.http["8765/api/state"] = {"port": "COM8", "port_open": True, "logged_in": True,
                                   "mode": "auto", "start_running": False, "error": None}
    host.http["8790/api/health"] = {"status": "ok", "app": "workbench"}
    host.tcp[8765] = True
    host.pids[8765] = 4380
    return agent, host


class TestSettingsAndToken(unittest.TestCase):
    def test_deep_merge_keeps_defaults(self):
        merged = wc.deep_merge(wc.DEFAULT_SETTINGS, {"bridge": {"start_timeout_seconds": 3}})
        self.assertEqual(merged["bridge"]["start_timeout_seconds"], 3)
        self.assertEqual(merged["gateway"]["port"], 8700)

    def test_token_created_and_stable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            self.assertTrue(Path(agent.token_path).exists())
            again = AgentForTest(agent.settings, FakeHost(), Path(tmp))
            self.assertEqual(agent.token, again.token)

    def test_loopback_classification(self):
        self.assertTrue(wc._is_loopback("127.0.0.1"))
        self.assertTrue(wc._is_loopback("127.0.1.1"))
        self.assertFalse(wc._is_loopback("172.25.0.1"))


class TestStatus(unittest.TestCase):
    def test_snapshot_shape_and_ports(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            snap = agent.status(fresh=True)
            self.assertEqual(snap["meta"]["service"], "wsl-control")
            self.assertEqual(len(snap["ports"]), 3)
            owners = {p["id"]: p["owner"] for p in snap["ports"]}
            self.assertEqual(owners["cco-main"], "serial-bridge")
            self.assertEqual(owners["factory"], "disabled")
            self.assertEqual(snap["bridge"]["windows"]["running"], True)
            self.assertEqual(snap["diag"]["task_state"], "Running")

    def test_owner_fc_diag_and_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            cfg = json.loads(host.files[agent.bridge_config()])
            cfg["ports"][0]["windows_com"] = "COM8"   # 与诊断服务同口
            cfg["ports"][1]["windows_com"] = "COM99"  # 系统里没有
            host.files[agent.bridge_config()] = json.dumps(cfg)
            snap = agent.status(fresh=True)
            owners = {p["id"]: p["owner"] for p in snap["ports"]}
            self.assertEqual(owners["cco-main"], "fc-diag")
            self.assertTrue([p for p in snap["ports"] if p["id"] == "cco-main"][0]["conflict"])
            self.assertEqual(owners["sta-main"], "absent")

    def test_cache_and_long_poll(self):
        import tempfile
        fake_now = [1000.0]
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp), clock=lambda: fake_now[0])
            first = agent.status(fresh=True)
            v1 = first["meta"]["version_no"]
            # TTL 内不再重collect
            orig = agent.collect
            calls = {"n": 0}

            def counting():
                calls["n"] += 1
                return orig()

            agent.collect = counting
            second = agent.status()
            self.assertEqual(calls["n"], 0)
            self.assertEqual(second["meta"]["version_no"], v1)
            # 过期后 wait_status 触发刷新
            fake_now[0] += 30
            done = {}

            def waiter():
                done["snap"] = agent.wait_status(since=v1, timeout=5)

            t = threading.Thread(target=waiter)
            t.start()
            time.sleep(0.3)
            agent.refresh()
            t.join(timeout=5)
            self.assertGreater(done["snap"]["meta"]["version_no"], v1)


class TestPortsAndServices(unittest.TestCase):
    def test_disable_port_rewrites_config_and_restarts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            result = agent.set_port_enabled("sta-main", False, restart=True)
            self.assertTrue(result["changed"])
            cfg = json.loads(host.files[agent.bridge_config()])
            self.assertFalse([p for p in cfg["ports"] if p["id"] == "sta-main"][0]["enabled"])
            self.assertTrue(host.files[agent.bridge_config() + ".bak-hostcontrol"])
            self.assertTrue(result["restarted"])
            # 停 + 起 => 进程操作被调用
            self.assertTrue(any("taskkill" in " ".join(r) for r in host.runs)
                            or host.spawned)

    def test_unknown_port(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            with self.assertRaises(wc.HostError) as ctx:
                agent.set_port_enabled("nope", False)
            self.assertEqual(ctx.exception.code, "PORT_NOT_FOUND")

    def test_bridge_start_reuses_running(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            out = agent.service_start("bridge")
            self.assertTrue(out["already_running"])

    def test_gateway_start_missing_paths(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            host.tcp[8700] = False
            with self.assertRaises(wc.HostError) as ctx:
                agent.service_start("gateway")
            self.assertEqual(ctx.exception.code, "GATEWAY_BUILD_PLAIN_MISSING")

    def test_diag_restart_uses_schtasks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            out = agent.service_restart("diag")
            self.assertEqual(out["action"], "restart")
            joined = [" ".join(r) for r in host.runs]
            self.assertTrue(any("schtasks" in j and "/End" in j for j in joined))
            self.assertTrue(any("schtasks" in j and "/Run" in j for j in joined))


class TestPortConfigCrud(unittest.TestCase):
    """端口映射增改（create/update/batch/coms）——配置结构化写回 + 按需重启。"""

    def test_create_port_with_auto_tcp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            result = agent.create_port({"id": "demo", "windows_com": "com27",
                                        "label": "演示口", "baudrate": 9600}, restart=False)
            self.assertTrue(result["changed"])
            item = result["created"]
            self.assertEqual(item["windows_com"], "COM27")   # 大小写归一
            self.assertEqual(item["tcp_port"], 8818)         # BASE_CONFIG 未写 tcp/control → 从头分配
            self.assertEqual(item["control_port"], 9818)
            cfg = json.loads(host.files[agent.bridge_config()])
            saved = [p for p in cfg["ports"] if p["id"] == "demo"][0]
            self.assertEqual(saved["parity"], "N")
            self.assertTrue(saved["enabled"])

    def test_create_restart_backs_up(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            result = agent.create_port({"id": "demo", "windows_com": "COM27"}, restart=True)
            self.assertTrue(result["restarted"])
            self.assertTrue(host.files[agent.bridge_config() + ".bak-hostcontrol"])

    def test_create_rejects_duplicate_id_and_com(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            with self.assertRaises(wc.HostError) as ctx:
                agent.create_port({"id": "cco-main", "windows_com": "COM27"}, restart=False)
            self.assertEqual(ctx.exception.code, "PORT_EXISTS")
            with self.assertRaises(wc.HostError) as ctx:
                agent.create_port({"id": "demo", "windows_com": "COM12"}, restart=False)
            self.assertEqual(ctx.exception.code, "COM_IN_USE")

    def test_create_rejects_bad_id_and_com(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            with self.assertRaises(wc.HostError) as ctx:
                agent.create_port({"id": "9bad", "windows_com": "COM27"}, restart=False)
            self.assertEqual(ctx.exception.code, "VALIDATION")
            with self.assertRaises(wc.HostError) as ctx:
                agent.create_port({"id": "demo", "windows_com": "LPT1"}, restart=False)
            self.assertEqual(ctx.exception.code, "VALIDATION")

    def test_update_label_only_skips_restart(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            result = agent.update_port("cco-main", {"label": "CCO 新名", "usage": "cco"},
                                       restart=True)
            self.assertTrue(result["changed"])
            self.assertFalse(result["restarted"])
            self.assertIn("无需重启", result["restart_skipped"])
            cfg = json.loads(host.files[agent.bridge_config()])
            self.assertEqual(cfg["ports"][0]["label"], "CCO 新名")

    def test_update_com_restarts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            result = agent.update_port("sta-main", {"windows_com": "com19", "baudrate": 9600,
                                                    "parity": "e"}, restart=True)
            self.assertTrue(result["restarted"])
            self.assertEqual(result["changes"]["windows_com"], "COM19")
            self.assertEqual(result["changes"]["parity"], "E")
            cfg = json.loads(host.files[agent.bridge_config()])
            saved = [p for p in cfg["ports"] if p["id"] == "sta-main"][0]
            self.assertEqual(saved["windows_com"], "COM19")
            self.assertEqual(saved["baudrate"], 9600)

    def test_update_no_change(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            result = agent.update_port("cco-main", {"label": "CCO", "windows_com": "COM12"},
                                       restart=False)
            self.assertFalse(result["changed"])

    def test_update_rejects_com_in_use(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            with self.assertRaises(wc.HostError) as ctx:
                agent.update_port("sta-main", {"windows_com": "COM12"}, restart=False)
            self.assertEqual(ctx.exception.code, "COM_IN_USE")

    def test_update_unknown_field_and_missing_port(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            with self.assertRaises(wc.HostError) as ctx:
                agent.update_port("cco-main", {"bogus": 1}, restart=False)
            self.assertEqual(ctx.exception.code, "VALIDATION")
            with self.assertRaises(wc.HostError) as ctx:
                agent.update_port("nope", {"label": "x"}, restart=False)
            self.assertEqual(ctx.exception.code, "PORT_NOT_FOUND")

    def test_batch_disable_enable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            result = agent.batch_ports("disable", restart=False)
            self.assertEqual(sorted(result["changed"]), ["cco-main", "sta-main"])
            self.assertEqual(result["unchanged"], ["factory"])  # 本来就禁用
            cfg = json.loads(host.files[agent.bridge_config()])
            self.assertTrue(all(not p["enabled"] for p in cfg["ports"]))
            result = agent.batch_ports("enable", ids=["factory"], restart=False)
            self.assertEqual(result["changed"], ["factory"])
            self.assertTrue(host.files[agent.bridge_config() + ".bak-hostcontrol"])

    def test_batch_restarts_only_once(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            before = len(host.runs)
            result = agent.batch_ports("disable", restart=True)
            self.assertTrue(result["restarted"])
            # 重启链 = wsl stop + wsl start + supervisor start，与单口开关相同，不会逐口翻倍
            self.assertEqual(len(host.runs) - before, 3)

    def test_batch_invalid_action_and_unknown_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            with self.assertRaises(wc.HostError) as ctx:
                agent.batch_ports("toggle", restart=False)
            self.assertEqual(ctx.exception.code, "VALIDATION")
            with self.assertRaises(wc.HostError) as ctx:
                agent.batch_ports("enable", ids=["nope"], restart=False)
            self.assertEqual(ctx.exception.code, "PORT_NOT_FOUND")

    def test_coms_view_flags(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            view = agent.coms_view(fresh=True)
            self.assertFalse(view["cached"])
            flags = {c["com"]: c for c in view["coms"]}
            self.assertEqual(flags["COM12"]["mapped_by"], "cco-main")
            self.assertFalse(flags["COM12"]["free"])
            self.assertEqual(flags["COM18"]["mapped_by"], "sta-main")
            cached = agent.coms_view(fresh=False)
            self.assertTrue(cached["cached"])
            self.assertTrue(cached["scanned_at"])


class TestDoctorAndLogs(unittest.TestCase):
    def test_doctor_flags_missing_com(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            host.coms = [{"com": "COM12", "device": "CP210x"}]  # COM18 不见了
            result = agent.doctor()
            ids = {c["id"]: c for c in result["checks"]}
            self.assertEqual(ids["bridge_coms"]["level"], "warn")
            self.assertIn("COM18", ids["bridge_coms"]["detail"])
            self.assertIn(result["verdict"], ("ok", "info", "warn", "error"))

    def test_log_sources(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, host = make_agent(Path(tmp))
            Path(host.files.setdefault(agent.bridge_log(), "")).parent.mkdir(parents=True, exist_ok=True)
            host.files[agent.bridge_log()] = "l1\nl2\nl3\n"
            out = agent.log_tail("bridge", 2)
            self.assertEqual(out["lines"], ["l2", "l3"])
            with self.assertRaises(wc.HostError):
                agent.log_tail("nope", 2)

    def test_audit_appends(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            agent, _ = make_agent(Path(tmp))
            agent.audit("service.start", "bridge", "172.25.0.9", True)
            agent.audit("service.stop", "bridge", "172.25.0.9", False, "boom")
            lines = agent.audit_tail(10)
            self.assertEqual(len(lines), 2)
            self.assertFalse(lines[1]["ok"])


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        cls.agent, cls.host = make_agent(Path(cls.tmp.name))
        wc.ControlHandler.agent = cls.agent
        cls.server = wc.ControlServer(("0.0.0.0", 0), wc.ControlHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, path, method="GET", body=None, token=None, host="127.0.0.1"):
        url = "http://%s:%d%s" % (host, self.port, path)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Host-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health_no_auth(self):
        code, payload = self.call("/health")
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])

    def test_status_and_caps(self):
        code, payload = self.call("/api/v1/status?fresh=1")
        self.assertEqual(code, 200)
        self.assertIn("ports", payload["data"])
        code, caps = self.call("/api/v1/caps")
        self.assertEqual(code, 200)
        self.assertEqual(caps["data"]["version"], wc.VERSION)

    def test_usbipd_removed(self):
        code, payload = self.call("/api/usbipd/map", method="POST")
        self.assertEqual(code, 410)
        self.assertEqual(payload["error"]["code"], "GONE")

    def test_ports_endpoint(self):
        code, payload = self.call("/api/v1/ports")
        self.assertEqual(code, 200)
        self.assertGreaterEqual(len(payload["data"]["ports"]), 3)

    def test_coms_endpoint_http(self):
        code, payload = self.call("/api/v1/coms?fresh=1")
        self.assertEqual(code, 200)
        coms = {c["com"]: c for c in payload["data"]["coms"]}
        self.assertEqual(coms["COM12"]["mapped_by"], "cco-main")
        self.assertFalse(coms["COM12"]["free"])
        self.assertTrue(coms["COM12"]["device"])

    def test_port_create_update_batch_http(self):
        code, payload = self.call("/api/v1/ports", method="POST", body={
            "id": "demo-http", "windows_com": "COM27", "label": "演示", "baudrate": 9600})
        self.assertEqual(code, 200, payload)
        data = payload["data"]
        self.assertTrue(data["restarted"])
        self.assertEqual(data["created"]["tcp_port"], 8818)
        code, payload = self.call("/api/v1/ports/demo-http", method="PATCH",
                                  body={"label": "演示2"})
        self.assertEqual(code, 200, payload)
        self.assertTrue(payload["data"]["changed"])
        code, payload = self.call("/api/v1/ports/batch", method="POST",
                                  body={"action": "disable"})
        self.assertEqual(code, 200, payload)
        self.assertIn("demo-http", payload["data"]["changed"])
        self.assertTrue(payload["data"]["restarted"])
        # 校验错误映射到 400
        code, payload = self.call("/api/v1/ports/demo-http", method="PATCH", body={"bogus": 1})
        self.assertEqual(code, 400)
        self.assertEqual(payload["error"]["code"], "VALIDATION")
        code, payload = self.call("/api/v1/ports", method="POST",
                                  body={"id": "demo-http", "windows_com": "COM99"})
        self.assertEqual(code, 409)
        self.assertEqual(payload["error"]["code"], "PORT_EXISTS")

    def test_port_toggle_http(self):
        code, payload = self.call("/api/v1/ports/factory/enable", method="POST", body={})
        self.assertEqual(code, 200, payload)
        self.assertTrue(payload["data"]["changed"])

    def test_service_and_doctor(self):
        code, payload = self.call("/api/v1/services/bridge/start", method="POST")
        self.assertEqual(code, 200)
        code, doc = self.call("/api/v1/doctor", method="POST")
        self.assertEqual(code, 200)
        self.assertIn("checks", doc["data"])

    def test_legacy_aliases(self):
        code, payload = self.call("/api/status")
        self.assertEqual(code, 200)
        code, payload = self.call("/api/bridge/status")
        self.assertEqual(code, 200)
        code, payload = self.call("/api/gateway/log?which=out")
        self.assertEqual(code, 404)  # 日志文件不存在

    def test_non_loopback_requires_token(self):
        ip = None
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except OSError:
            ip = None
        if not ip or ip.startswith("127."):
            self.skipTest("没有非回环地址可用")
        code, payload = self.call("/api/v1/status", host=ip)
        self.assertEqual(code, 401)
        self.assertEqual(payload["error"]["code"], "UNAUTHORIZED")
        code, payload = self.call("/api/v1/status", host=ip, token=self.agent.token)
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main()
