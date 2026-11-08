"""上下文工程 CLI 的冒烟测试（不依赖 seekdb / PowerContext 在线）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_ctx(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base = dict(os.environ)
    base.update({"CTX_STORE_BACKEND": "local", "CTX_STATE_BACKEND": "json"})
    base.pop("MINIMAX_API_KEY", None)
    if env:
        base.update(env)
    return subprocess.run(
        [sys.executable, "-m", "ctx.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=base,
    )


def test_doctor_reports_backend_and_fix_hints():
    proc = run_ctx("doctor")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout[proc.stdout.index("{") :])
    checks = payload["checks"]
    # 本地兜底时也要说清后端与规模，不能假装连上了 seekdb
    assert checks["store"]["backend"] == "local-fts5"
    assert checks["store"]["documents"] > 0
    assert "seekdb_error" in checks["store"]
    # 无 Key 时明确给出可执行的后续动作
    assert checks["minimax"]["api_key"] == "missing"
    assert payload["verdict"] in ("PASS", "WARN")
    assert any("MINIMAX_API_KEY" in hint for hint in payload["fix"])


def test_search_returns_ranked_hits_with_reason():
    proc = run_ctx("search", "--query", "尾差 分摊", "--k", "3")
    assert proc.returncode == 0, proc.stderr
    hits = json.loads(proc.stdout)
    assert hits, "检索结果为空"
    assert hits[0]["score"] > 0
    assert hits[0]["chunk_id"]
    # 排序必须单调不增，否则"可解释重排"就是空话
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_verify_without_model_records_fails_loudly():
    proc = run_ctx("verify", "--task", "REQ-2026-011")
    payload = json.loads(proc.stdout[proc.stdout.index("{") :])
    assert payload["verdict"] in ("PASS", "FAIL")
    assert "tests" in payload and payload["tests"]["ok"] is True
