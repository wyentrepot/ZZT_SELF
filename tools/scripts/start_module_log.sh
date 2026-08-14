#!/usr/bin/env bash
# 启动模块日志 / 烧录串口服务（端口 8766）。
# 用法：bash tools/scripts/start_module_log.sh
# 幂等：端口已被占用时直接提示已运行，不重复启动。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT=8766

# 已运行则直接退出（端口探测）
if curl -s -o /dev/null "http://127.0.0.1:${PORT}/api/version" 2>/dev/null; then
  echo "模块日志服务已在运行：http://127.0.0.1:${PORT}/module-serial"
  exit 0
fi

# 找到 python3
PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  echo "[错误] 未找到 python3" >&2
  exit 1
fi

export PYTHONPATH="${REPO_ROOT}/apps:${REPO_ROOT}/libs"

echo "启动模块日志服务 → http://127.0.0.1:${PORT}/module-serial"
exec "$PYTHON" -m module_log.run
