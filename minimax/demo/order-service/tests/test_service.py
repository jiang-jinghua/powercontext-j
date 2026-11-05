from decimal import Decimal

import pytest

from order_service import LineItem, UnknownCouponError, quote


def item(amount: str) -> LineItem:
    return LineItem(sku="SKU-1", unit_price=Decimal(amount), quantity=1)


def test_quote_without_coupon_applies_member_discount() -> None:
    priced = quote([item("250.00")])
    assert priced.member_discount == Decimal("25.00")
    assert priced.coupon_discount == Decimal("0.00")
    assert priced.shipping == Decimal("12.00")
    assert priced.total == Decimal("237.00")


def test_quote_is_free_shipping_above_threshold() -> None:
    priced = quote([item("320.00")])
    assert priced.member_discount == Decimal("32.00")
    assert priced.shipping == Decimal("0.00")
    assert priced.total == Decimal("288.00")


def test_unknown_coupon_is_rejected() -> None:
    with pytest.raises(UnknownCouponError):
        quote([item("100.00")], coupon_code="NOT-A-COUPON")


def test_empty_cart_is_rejected() -> None:
    with pytest.raises(ValueError):
        quote([])
