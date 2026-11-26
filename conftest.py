"""仓库级 pytest 配置：把仓库根、apps/、libs/ 加入 sys.path。

目录分层后包结构为 apps/（listener、module_log）与 libs/（shared、parser_lib、
loghooks、sim_concentrator），测试文件分布在各项目内。此 conftest 确保从任意
目录运行 pytest 都能 import 这些顶层包（导入名保持 shared.* / listener.* 不变）。
"""
import concurrent.futures
import concurrent.futures.thread  # noqa: F401 - 显式加载子模块，避免收尾时属性访问触发懒加载报错
import ctypes
import gc
import os
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
for _sub in ("", "apps", "libs"):
    _p = os.path.join(_REPO_ROOT, _sub) if _sub else _REPO_ROOT
    if _p not in sys.path:
        sys.path.insert(0, _p)


def hplc_test_data_root():
    """Return external HPLC fixtures when configured, else the legacy root.

    This helper is intentionally test-only: application packages must never
    discover or read fixture data through an environment variable.
    """
    configured = os.environ.get("HPLC_TEST_DATA_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(_REPO_ROOT) / "测试文件"

from shared.infra import ensure_paths  # noqa: E402

ensure_paths()


@pytest.fixture(autouse=True, scope="session")
def _dont_join_thread_pools_at_exit():
    """解释器退出不 join 线程池：卡在 30s wait 的观察任务会让退出挂几十秒。

    concurrent.futures.thread 在 threading 的 atexit 里注册了 _python_exit，
    会逐个 join 非 daemon 工作线程；配合 _release_fastapi_app_caches 的
    cancel_futures（清掉排队积压），这里再把 join 回调摘除，剩余在跑的
    任务随进程退出即止。仅测试进程内生效。
    """
    yield
    callback = getattr(concurrent.futures.thread, "_python_exit", None)
    if callback is None:
        return
    # 3.9-3.11 叫 _threading_atexits（元素是 functools.partial）；3.12+ 叫
    # _threading_atexit_callbacks（元素是原函数）。按「解包后是否同一函数」识别。
    for attr in ("_threading_atexits", "_threading_atexit_callbacks"):
        callbacks = getattr(threading, attr, None)
        if not callbacks:
            continue
        callbacks[:] = [p for p in callbacks if getattr(p, "func", p) is not callback]


@pytest.fixture(autouse=True)
def _release_fastapi_app_caches():
    """每个测试结束后释放被 fastapi 内部缓存钉住的 app 图（防测试内存累积）。

    背景：``create_*_app()`` 每次调用都会为闭包端点生成新的 callable，而
    fastapi.dependencies.models 的三个 ``lru_cache``（``_is_gen_callable_cached`` /
    ``_is_async_gen_callable_cached`` / ``_is_coroutine_callable_cached``，maxsize=4096）
    以这些 callable 为键缓存判定结果——键持有 app 的引用，整个 app 图（路由、
    pydantic 校验器，单个 ~135MB）随之被钉住，gc 无法回收。凡「每测试新建 app」
    的测试文件，用例一多就会把进程推到数 GB（实测 40 用例峰值 4.5GB）。

    缓存里只是 isinstance 判定的布尔结果，清空仅损失微秒级重算；随后 gc 回收
    循环引用、malloc_trim 把已释放堆还给 OS。仅测试进程内生效，不影响运行期服务。

    顺带收尾遗留的 ThreadPoolExecutor（如 AICapabilityService 的 ai-v2 池）：
    其工作线程非 daemon，Python 退出时 atexit 会逐个 join，排队的 live 窗口/
    烧录任务会让解释器挂起几十秒以上。测后 cancel 未开跑的任务、不等待在跑
    任务，既不破坏已完成的断言，又让进程能正常退出。
    """
    yield
    models = sys.modules.get("fastapi.dependencies.models")
    if models is not None:
        for name in ("_is_gen_callable_cached",
                     "_is_async_gen_callable_cached",
                     "_is_coroutine_callable_cached"):
            cached = getattr(models, name, None)
            if cached is not None and hasattr(cached, "cache_clear"):
                cached.cache_clear()
    for obj in gc.get_objects():
        if type(obj).__name__ == "ThreadPoolExecutor":
            try:
                obj.shutdown(wait=False, cancel_futures=True)
            except Exception:  # noqa: BLE001 - 收尾失败不抬测试
                pass
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 - 非 Linux/无 libc 时静默跳过
        pass
