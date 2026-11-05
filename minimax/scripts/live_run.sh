#!/usr/bin/env bash
# One-shot live run: requires a real MiniMax API key.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
: "${MINIMAX_API_KEY:?export MINIMAX_API_KEY before running this script}"
PYTHON="${PYTHON:-python3}"

echo "api style: ${MINIMAX_API_STYLE:-native}"
echo "base url : ${MINIMAX_BASE_URL:-<default>}"
echo "model    : ${MINIMAX_MODEL:-MiniMax-M3.1-Flash}"

echo "== reset =="
"$PYTHON" -m sddctx reset
echo "== init/import =="
"$PYTHON" -m sddctx init
"$PYTHON" -m sddctx import
echo "== MiniMax run =="
"$PYTHON" -m sddctx run T-001 --provider minimax
echo "== state =="
"$PYTHON" -m sddctx status T-001
echo "== regenerate the technical record =="
"$PYTHON" scripts/make_record.py
