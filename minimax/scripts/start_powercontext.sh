#!/usr/bin/env bash
# 启动本地 PowerContext Server（SQLite 内置运行时，无需模型即可保存/召回转接状态）。
# 说明：PowerContext 的 server 命令未在 CLI 顶层暴露，这里通过其 typer app 直接启动，
#       与官方 `powercontext` 1.5.0 包内的 server.cli 行为一致。
set -euo pipefail
PY="${PY:-.venv/Scripts/python.exe}"
PORT="${POWERCONTEXT_PORT:-8077}"

"$PY" - "$PORT" <<'PY' &
import sys
from powercontext.server.cli import app
port = sys.argv[1]
sys.argv = ["powercontext", "run", "--no-env-file", "--host", "127.0.0.1", "--port", str(port)]
app()
PY

echo -n "[powercontext] 等待 http://127.0.0.1:${PORT}/health/live "
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${PORT}/health/live" >/dev/null 2>&1; then
    echo "→ ready"
    curl -s "http://127.0.0.1:${PORT}/v1/scopes" | head -c 300; echo
    exit 0
  fi
  echo -n "."; sleep 1
done
echo " → 超时"
exit 1
