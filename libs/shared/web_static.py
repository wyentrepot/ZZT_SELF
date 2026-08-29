"""静态文件挂载辅助：HTML 页面禁用浏览器缓存。

工具页（module-serial 等）的 HTML 内嵌易变内容（默认任务 JSON、按钮逻辑
版本号），被浏览器启发式缓存后会出现"改了代码页面还是旧"的问题。
此挂载类对 text/html 响应统一加 Cache-Control: no-cache（每次回源校验，
未变化返回 304，不浪费流量）；CSS/JS 仍由引用处的 ?v= 版本号控制。
"""
from __future__ import annotations

from fastapi.staticfiles import StaticFiles


class NoCacheHTMLStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):  # noqa: ANN002, ANN003
        response = super().file_response(*args, **kwargs)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response
