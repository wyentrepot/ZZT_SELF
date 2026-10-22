#!/usr/bin/env python3
"""Call the workbench serial-bridge endpoints and print JSON.

Usage:
  python verify_bridge.py                 # POST /api/serial-bridge/verify (open check)
  python verify_bridge.py --status        # GET  /api/serial-bridge/status
  python verify_bridge.py --ports listener cco-main
  python verify_bridge.py --no-open       # skip pyserial open check
  python verify_bridge.py --base-url http://127.0.0.1:8790
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _request(url: str, method: str = "GET", payload: dict | None = None,
             timeout: float = 10.0) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the Windows->WSL serial bridge via workbench")
    parser.add_argument("--base-url", default="http://127.0.0.1:8790",
                        help="workbench base URL (default: http://127.0.0.1:8790)")
    parser.add_argument("--status", action="store_true",
                        help="read-only status instead of verify")
    parser.add_argument("--ports", nargs="*", default=None,
                        help="port ids (default: all enabled)")
    parser.add_argument("--no-open", action="store_true",
                        help="skip the pyserial open check")
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    try:
        if args.status:
            body = _request(base + "/api/serial-bridge/status")
        else:
            payload: dict = {"open": not args.no_open}
            if args.ports:
                payload["ports"] = args.ports
            body = _request(base + "/api/serial-bridge/verify", "POST", payload)
    except urllib.error.URLError as exc:
        print(f"workbench 不可达（{base}）：{exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as exc:
        print(f"请求失败：{exc}", file=sys.stderr)
        return 2

    print(json.dumps(body, ensure_ascii=False, indent=2))
    if not body.get("ok"):
        bad = [p for p in (body.get("ports") or [])
               if not (p.get("channel_ok") and p.get("workbench_ready"))]
        if bad:
            print("\n未就绪端口（先别开工作台串口会话）：", file=sys.stderr)
            for p in bad:
                msgs = "; ".join(str(x) for x in (p.get("errors") or p.get("warnings") or []))
                print(f"  - {p.get('id')}: channel_ok={p.get('channel_ok')} "
                      f"workbench_ready={p.get('workbench_ready')} {msgs[:160]}", file=sys.stderr)
        print("下一步：supervisor.running=false → 起 WSL 侧桥；client_connected=false → Windows 侧桥没起；"
              "open_ok=false 多为 parity 写成 E（PTY 侧必须 N）。", file=sys.stderr)
    return 0 if body.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
