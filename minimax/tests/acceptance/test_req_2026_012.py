"""REQ-2026-012 验收用例（规格 v2 追加项 SPEC-011-06）。

实现前：失败（红）；实现后：通过（绿）。
回归口径：本文件只新增用例，不修改既有断言。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from settlement import service
from settlement.engine import refund, settle

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures"


def load(name: str):
    return service.load_order(FIXTURES / name)


def test_spec_011_06_remaining_paid_exposed():
    """SPEC-011-06：部分退货后暴露剩余实付，等于未退货行实付合计。"""
    order = load("order_standard.json")
    result = refund(order, settle(order), [1, 2])
    assert result.remaining_paid_cents == 4500
    assert result.to_dict()["remaining_paid_cents"] == 4500


def test_spec_011_06_full_zero_paid_order():
    """SPEC-011-06 边界：优惠全额抵扣的订单，退 1、2 后剩余实付为 0。"""
    order = load("order_fullpay.json")
    result = refund(order, settle(order), [1, 2])
    assert result.remaining_paid_cents == 0


def test_spec_011_06_all_returned_is_zero():
    """SPEC-011-06 边界：整单全退时剩余实付为 0。"""
    order = load("order_standard.json")
    assert refund(order, settle(order), [1, 2, 3]).remaining_paid_cents == 0


def test_spec_011_06_cli_output_contains_field():
    """SPEC-011-06：CLI 输出包含 remaining_paid_cents，且不影响退款总额。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "settlement.cli",
            "refund",
            "--order",
            str(FIXTURES / "order_standard.json"),
            "--return",
            "1,2",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["remaining_paid_cents"] == 4500
    assert payload["refund_total_cents"] == 90000 + 18000
