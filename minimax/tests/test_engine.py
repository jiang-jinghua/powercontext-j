"""结算与退款回归测试（基线行为，不可被新需求破坏）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from settlement import service
from settlement.engine import build_order, refund, settle
from settlement.errors import RefundError, ValidationError

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str):
    return service.load_order(FIXTURES / name)


def test_settle_standard_order_amounts():
    order = load("order_standard.json")
    result = settle(order)
    assert result.gross_cents == 125000
    assert result.coupon_discount_cents == 10000
    assert result.subsidy_cents == 2500
    assert [ln.paid_cents for ln in result.lines] == [90000, 18000, 4500]
    assert [ln.discount_cents for ln in result.lines] == [10000, 2000, 500]
    assert result.paid_total_cents == 112500 + 1200


def test_settle_is_deterministic():
    order = load("order_standard.json")
    first = settle(order).to_dict()
    second = settle(order).to_dict()
    assert first == second


def test_refund_all_lines_returns_shipping():
    order = load("order_standard.json")
    result = refund(order, settle(order), [1, 2, 3])
    assert [ln.refund_cents for ln in result.lines] == [90000, 18000, 4500]
    assert result.shipping_refund_cents == 1200
    assert result.refund_total_cents == 113700


def test_refund_partial_lines_excludes_shipping():
    order = load("order_standard.json")
    result = refund(order, settle(order), [1])
    assert result.to_dict()["lines"] == [
        {"line_no": 1, "sku": "SKU-A", "refund_cents": 90000}
    ]
    assert result.shipping_refund_cents == 0
    assert result.refund_total_cents == 90000


def test_refund_deduplicates_lines_and_keeps_order():
    order = load("order_standard.json")
    result = refund(order, settle(order), [3, 1, 3])
    assert result.returned_line_nos == (3, 1)
    assert result.refund_total_cents == 94500


def test_refund_unknown_line_raises():
    order = load("order_standard.json")
    with pytest.raises(RefundError) as exc:
        refund(order, settle(order), [9])
    assert exc.value.code == "E_REFUND"


def test_duplicate_line_no_rejected():
    payload = json.loads((FIXTURES / "order_standard.json").read_text(encoding="utf-8"))
    payload["items"][1]["line_no"] = 1
    with pytest.raises(ValidationError) as exc:
        settle(build_order(payload))
    assert exc.value.code == "E_BAD_INPUT"


def test_coupon_below_min_spend_gives_no_discount():
    payload = json.loads((FIXTURES / "order_standard.json").read_text(encoding="utf-8"))
    payload["coupon"]["min_spend_cents"] = 999999
    result = settle(build_order(payload))
    assert result.coupon_discount_cents == 0
    # 券不满足门槛，但平台补贴仍然生效
    assert result.paid_total_cents == 125000 - 2500 + 1200
