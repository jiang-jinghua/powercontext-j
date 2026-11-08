"""服务层与 CLI 回归测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "settlement.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_cli_settle_outputs_json():
    proc = run_cli("settle", "--order", str(FIXTURES / "order_standard.json"))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["order_id"] == "SO20261107001"
    assert payload["paid_total_cents"] == 113700


def test_cli_refund_partial():
    proc = run_cli(
        "refund",
        "--order",
        str(FIXTURES / "order_standard.json"),
        "--return",
        "2,3",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["refund_total_cents"] == 22500
    assert payload["shipping_refund_cents"] == 0


def test_cli_error_returns_exit_code_2():
    proc = run_cli(
        "refund",
        "--order",
        str(FIXTURES / "order_standard.json"),
        "--return",
        "8",
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["error_code"] == "E_REFUND"
