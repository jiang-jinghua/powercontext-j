#!/usr/bin/env bash
# End-to-end demo: T-001 -> state reuse -> spec update -> T-002 context.
# Set SDD_PROVIDER=minimax and MINIMAX_API_KEY to run the real model call.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src"
PYTHON="${PYTHON:-python3}"
PROVIDER="${SDD_PROVIDER:-manual}"

echo "== reset =="
"$PYTHON" -m sddctx reset

echo "== init + import (spec 2026.1) =="
"$PYTHON" -m sddctx init
"$PYTHON" -m sddctx import

echo "== context T-001 =="
"$PYTHON" -m sddctx context T-001 --json > /tmp/sdd-context-T-001.json
"$PYTHON" - <<'PY'
import json
data = json.load(open("/tmp/sdd-context-T-001.json", encoding="utf-8"))
print("spec:", [hit["symbol"] for hit in data["spec"]])
print("code:", [hit["symbol"] for hit in data["code"]])
print("tests:", len(data["tests"]), "tokens:", data["tokens"], "db_rows:", data["db_rows"])
PY

echo "== run T-001 ($PROVIDER) =="
if [ "$PROVIDER" = "minimax" ]; then
  "$PYTHON" -m sddctx run T-001 --provider minimax
else
  "$PYTHON" -m sddctx run T-001 --provider manual --patch patches/T-001.patch
fi

echo "== status after process restart =="
"$PYTHON" -m sddctx status T-001

echo "== spec update to 2026.2 =="
"$PYTHON" -m sddctx import --spec-version 2026.2 \
  --spec-overlay demo/spec-updates/2026.2/promotion.md

echo "== context T-002 (new spec + reused state) =="
"$PYTHON" -m sddctx context T-002 --json > /tmp/sdd-context-T-002.json
"$PYTHON" - <<'PY'
import json
data = json.load(open("/tmp/sdd-context-T-002.json", encoding="utf-8"))
clause = next(hit for hit in data["spec"] if hit["symbol"] == "SPEC-PRM-002")
print("SPEC-PRM-002 contains 150:", "150" in clause["text"])
print("state records:", [(row["kind"], row["status"]) for row in data["state"]])
PY
