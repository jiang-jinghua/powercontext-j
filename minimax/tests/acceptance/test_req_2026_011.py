"""REQ-2026-011 验收用例（对应规格 SPEC-011-01 ~ SPEC-011-05）。

实现前：全部失败（红）。
实现后：全部通过（绿）。
本文件同时是评审的「验收样例」，用例名与规格条目一一对应。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from settlement import service
from settlement.allocation import allocate
from settlement.engine import build_order, preview_refund, refund, settle
from settlement.errors import NegativeAllocationError, NegativeRefundError
from settlement.models import RefundLine
from settlement.validators import validate_refund_amounts

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "src"
FIXTURES = ROOT / "tests" / "fixtures"


def load(name: str):
    return service.load_order(FIXTURES / name)


def raw(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- SPEC-011-01


def test_spec_011_01_excluded_category_gets_no_allocation():
    """SPEC-011-01：被排除类目分摊额为 0，份额按剩余行基数重分配，整单口径不变。"""
    result = settle(load("order_excluded_gift.json"))
    got = [(ln.discount_cents, ln.paid_cents) for ln in result.lines]
    assert got == [(10417, 89583), (2083, 17917), (0, 5000)]
    assert result.paid_total_cents == 112500 + 1200
    assert result.unallocated_discount_cents == 0


def test_spec_011_01_all_categories_excluded_keeps_gross():
    """SPEC-011-01 边界：全部行被排除时不抛异常，待分摊额记为整单未分摊优惠。"""
    payload = raw("order_excluded_gift.json")
    payload["coupon"]["excluded_categories"] = ["digital", "book", "gift"]
    result = settle(build_order(payload))
    assert [ln.discount_cents for ln in result.lines] == [0, 0, 0]
    assert [ln.paid_cents for ln in result.lines] == [100000, 20000, 5000]
    assert result.unallocated_discount_cents == 10000 + 2500
    assert result.paid_total_cents == 125000 - 12500 + 1200


def test_spec_011_01_percent_coupon_respects_exclusions():
    """SPEC-011-01：折扣券（percent）与满减共用同一套类目排除语义。"""
    payload = raw("order_excluded_gift.json")
    payload["coupon"] = {
        "code": "CPN_PCT",
        "type": "percent",
        "value": 88,
        "min_spend_cents": 0,
        "excluded_categories": ["gift"],
    }
    result = settle(build_order(payload))
    assert result.coupon_discount_cents == 15000
    # 待分摊额 = 券 15000 + 平台补贴 2500 = 17500，全部落在未排除的两行上
    assert [(ln.discount_cents, ln.paid_cents) for ln in result.lines] == [
        (14584, 85416),
        (2916, 17084),
        (0, 5000),
    ]


# ---------------------------------------------------------------- SPEC-011-02


def test_spec_011_02_remainder_is_spread_one_cent_per_line():
    """SPEC-011-02：尾差按 1 分/行轮转分配，替代"全部分给单行"。"""
    assert allocate(8, [(1, 1), (2, 1), (3, 1)]) == {1: 3, 2: 3, 3: 2}


def test_spec_011_02_tail_order_fixture():
    """SPEC-011-02：端到端验证尾差轮转后的行实付，行和等于整单实付。"""
    result = settle(load("order_tail.json"))
    assert [ln.discount_cents for ln in result.lines] == [3334, 3334, 3333]
    assert [ln.paid_cents for ln in result.lines] == [6666, 6666, 6667]
    assert result.paid_total_cents == 30000 - 10001


# ---------------------------------------------------------------- SPEC-011-03


def test_spec_011_03_shipping_refunded_when_remaining_paid_is_zero():
    """SPEC-011-03：退货后剩余实付为 0 时退还全额运费。"""
    order = load("order_fullpay.json")
    settlement = settle(order)
    assert [ln.paid_cents for ln in settlement.lines] == [0, 0, 0]
    result = refund(order, settlement, [1, 2])
    assert result.shipping_refund_cents == 1200
    assert result.refund_total_cents == 1200


def test_spec_011_03_shipping_not_refunded_when_remaining_paid_left():
    """SPEC-011-03：仍有剩余实付时不退运费（与基线行为一致）。"""
    order = load("order_standard.json")
    result = refund(order, settle(order), [1, 2])
    assert result.shipping_refund_cents == 0


# ---------------------------------------------------------------- SPEC-011-04


def test_spec_011_04_negative_allocation_raises():
    """SPEC-011-04：分摊后行实付为负必须显式失败（E_NEG_ALLOC），不得静默夹断。"""
    with pytest.raises(NegativeAllocationError) as exc:
        settle(load("order_negative.json"))
    assert exc.value.code == "E_NEG_ALLOC"
    assert exc.value.detail["line_no"] == 2
    assert exc.value.detail["paid_cents"] == -30000


def test_spec_011_04_negative_refund_raises():
    """SPEC-011-04：退款明细为负必须抛 E_NEG_REFUND。"""
    with pytest.raises(NegativeRefundError) as exc:
        validate_refund_amounts("SO1", [RefundLine(line_no=1, sku="SKU-A", refund_cents=-1)])
    assert exc.value.code == "E_NEG_REFUND"


# ---------------------------------------------------------------- SPEC-011-05


def test_spec_011_05_preview_matches_settle_then_refund():
    """SPEC-011-05：预览结果与"先 settle 再 refund"逐字段一致。"""
    order = load("order_excluded_gift.json")
    expected = refund(order, settle(order), [1, 3]).to_dict()
    assert preview_refund(order, [1, 3]).to_dict() == expected


def test_spec_011_05_preview_service_and_cli():
    """SPEC-011-05：服务层与 CLI 都提供预览入口，且只读无副作用。"""
    assert (
        service.preview_refund_file(FIXTURES / "order_excluded_gift.json", [3])["refund_total_cents"]
        == 5000
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "settlement.cli",
            "preview",
            "--order",
            str(FIXTURES / "order_excluded_gift.json"),
            "--return",
            "1,3",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["refund_total_cents"] == 89583 + 5000
