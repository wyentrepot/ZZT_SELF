"""REQS-0043 工位 I1：workbench.json pid 写入侧防双起探针单测。

E3 实锤：旧实例持有 8790 时，两次新启动都在 uvicorn 绑定之前覆写了
``data/runtime/workbench.json``（留下死 pid 271064/293294，误导 case_run
服务发现）。run.py / desktop.py 现在先探测端口，已有活监听 → 拒绝双起、
不碰运行时文件。本文件只测探针本身（空闲端口 False / 有监听 True），
**绝不**真写运行时文件。
"""
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "apps"), str(REPO_ROOT / "libs")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from workbench.run import _port_already_served  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_probe_false_when_port_free():
    """空闲端口（内核分配后即关）→ 探不到监听 → False。"""
    port = _free_port()
    assert _port_already_served(port=port) is False


def test_probe_true_when_something_listens():
    """真有监听套接字 → True（与 8790 场景同构，但不占用生产端口）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert _port_already_served(port=port) is True


def test_probe_unroutable_address_is_false():
    """连接被拒（无监听）走 OSError 分支 → False，绝不抛异常。"""
    assert _port_already_served(port=1, host="127.0.0.1", timeout=0.2) is False
