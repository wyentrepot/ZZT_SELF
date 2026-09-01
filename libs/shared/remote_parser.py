"""Remote Windows parse-service client implementing the DllParser protocol.

WSL/Linux 侧没有 net8.0 解析库构建产物时，通过本客户端把深度解析委托给
Windows 上运行的 apps/parser_service（net48 GwHPLCAnalysis.dll）。

实现 ``shared.parser_service.DllParser`` 协议（parse_simple/parse_full/version），
使 ``ParserService`` 可无差别使用本地或远程解析后端；enrich 仍由 WSL 侧
``ParserService`` 完成，远程只返回 raw simple/full JSON 字符串。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import httpx

from shared.parser_service import DllParser


class RemoteParseError(RuntimeError):
    """远程解析服务不可用或返回了非预期结果。"""


def _default_config_path() -> Path:
    # libs/shared/remote_parser.py → 仓库根/config/remote_parse.json
    return Path(__file__).resolve().parent.parent.parent / "config" / "remote_parse.json"


def resolve_remote_parse_url(
    env=None, config_path: Optional[Path] = None
) -> Optional[str]:
    """解析远程服务地址：优先 ``HPLC_REMOTE_PARSE_URL``，其次 config/remote_parse.json。

    均未配置或配置为空时返回 None（表示不启用远程解析）。
    """
    env = os.environ if env is None else env
    url = (env.get("HPLC_REMOTE_PARSE_URL") or "").strip()
    if url:
        return url
    path = Path(config_path) if config_path is not None else _default_config_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            url = (data.get("url") or "").strip()
        except (OSError, ValueError):
            return None
    return url or None


class RemoteHplcParser:
    """把解析委托给 Windows 解析服务的 HTTP 客户端。

    每帧只发一次 ``POST /api/parse``（返回 raw simple/full 两个 JSON 字符串）；
    ``parse_simple``/``parse_full`` 共享本次请求的缓存，避免同帧两次往返。
    ``ParserService`` 内部用锁串行化调用，本实例缓存无需额外并发保护。
    """

    def __init__(self, base_url: str, timeout: float = 10.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        kwargs = {"timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        self._client = httpx.Client(base_url=self.base_url, **kwargs)
        self._cache: Optional[tuple[bytes, str, str]] = None

    def close(self) -> None:
        self._client.close()

    def version(self) -> dict:
        resp = self._client.get("/api/version")
        self._raise(resp)
        return resp.json()

    def health(self) -> dict:
        resp = self._client.get("/health")
        self._raise(resp)
        return resp.json()

    def parse_simple(self, frame: bytes) -> str:
        return self._fetch(frame)[1]

    def parse_full(self, frame: bytes) -> str:
        return self._fetch(frame)[2]

    def _fetch(self, frame: bytes) -> tuple[bytes, str, str]:
        if self._cache is not None and self._cache[0] == frame:
            return self._cache
        resp = self._client.post("/api/parse", json={"hex": frame.hex()})
        self._raise(resp)
        try:
            data = resp.json()
            simple, full = data["simple"], data["full"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RemoteParseError(f"远程解析返回缺少 simple/full：{resp.text!r}") from exc
        self._cache = (frame, simple, full)
        return self._cache

    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        if resp.status_code == 503:
            raise RemoteParseError("远程解析服务不可用（DLL 未加载或服务未就绪）")
        if resp.status_code == 422:
            raise RemoteParseError(f"远程帧校验失败：{resp.text}")
        try:
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RemoteParseError(f"远程解析请求失败：{exc}") from exc
