"""Windows 解析服务启动入口：uvicorn 0.0.0.0:8700。

用法：python -m parser_service.run（PYTHONPATH 需含 apps/ 与 libs/）

绑定默认 0.0.0.0：WSL2 需经 Windows 宿主地址（如 172.25.0.1）访问，
仅绑 127.0.0.1 时 WSL 的 localhost 转发常不可达。可用 HPLC_PARSE_HOST
环境变量覆盖（如 127.0.0.1 仅本机）。
"""
import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("HPLC_PARSE_HOST", "0.0.0.0")
    uvicorn.run("parser_service.app:app", host=host, port=8700)
