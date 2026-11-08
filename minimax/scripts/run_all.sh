#!/usr/bin/env bash
# 一键跑通：入库 → 任务闭环 → 新会话恢复 → 自检
# 用法：
#   bash scripts/run_all.sh                 # 用 .env 里的配置（真实 MiniMax）
#   CTX_PROVIDER=recorded bash scripts/run_all.sh   # 无 Key 时回放归档响应
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PY:-.venv/Scripts/python.exe}"
[ -f .env ] && set -a && . ./.env && set +a

echo "== 1/5 检查存储后端"
"$PY" -m ctx.cli stats || true

echo "== 2/5 入库工程资料（规格/任务卡/源码/测试/文档）"
"$PY" -m ctx.cli ingest --version "$(date +%Y%m%dT%H%M%S)"

echo "== 3/5 执行 REQ-2026-011 任务闭环"
"$PY" -m ctx.cli run --task REQ-2026-011 --provider "${CTX_PROVIDER:-minimax}"

echo "== 4/5 新会话恢复状态（模拟换进程/换机器）"
"$PY" -m ctx.cli resume --task REQ-2026-011 | head -20

echo "== 5/5 验收自检"
"$PY" -m ctx.cli verify --task REQ-2026-011
