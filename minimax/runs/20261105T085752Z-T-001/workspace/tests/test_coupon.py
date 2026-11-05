"""Target behaviour for T-001: SAVE30 coupon and HALF_UP rounding.

These tests fail before the change and must pass after it. They are part of the
delivered test set; the change must not edit this file.
"""

from decimal import Decimal

import pytest

from order_service import CouponNotApplicableError, LineItem, quote, round_money


def item(amount: str) -> LineItem:
    return LineItem(sku="SKU-1", unit_price=Decimal(amount), quantity=1)


def test_save30_applies_at_threshold() -> None:
    priced = quote([item("200.00")], coupon_code="SAVE30")
    assert priced.coupon_discount == Decimal("30.00")
    assert priced.total == Decimal("162.00")


def test_save30_is_rejected_below_threshold() -> None:
    with pytest.raises(CouponNotApplicableError):
        quote([item("199.99")], coupon_code="SAVE30")


def test_save30_threshold_excludes_shipping() -> None:
    # 195 + 12 shipping = 207; the threshold must still judge on 195 goods only.
    with pytest.raises(CouponNotApplicableError):
        quote([item("195.00")], coupon_code="SAVE30")


def test_save30_stacks_after_member_discount() -> None:
    priced = quote([item("250.00")], coupon_code="SAVE30")
    assert priced.member_discount == Decimal("25.00")
    assert priced.coupon_discount == Decimal("30.00")
    assert priced.total == Decimal("207.00")


def test_save30_does_not_reduce_shipping() -> None:
    priced = quote([item("250.00")], coupon_code="SAVE30")
    assert priced.shipping == Decimal("12.00")


def test_money_rounding_is_half_up() -> None:
    assert round_money("0.125") == Decimal("0.13")
    assert round_money("1.005") == Decimal("1.01")
