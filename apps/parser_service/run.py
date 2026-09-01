"""Windows 解析服务启动入口：uvicorn 127.0.0.1:8700。

用法：python -m parser_service.run（PYTHONPATH 需含 apps/ 与 libs/）
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("parser_service.app:app", host="127.0.0.1", port=8700)
