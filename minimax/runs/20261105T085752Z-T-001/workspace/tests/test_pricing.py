from decimal import Decimal

from order_service.pricing import LineItem, member_discount, shipping_fee, subtotal


def line(price: str, quantity: int = 1) -> LineItem:
    return LineItem(sku="SKU-1", unit_price=Decimal(price), quantity=quantity)


def test_subtotal_sums_line_amounts() -> None:
    items = [line("19.99", 3), line("5.00", 2)]
    assert subtotal(items) == Decimal("69.97")


def test_shipping_is_free_at_threshold() -> None:
    assert shipping_fee(Decimal("300.00")) == Decimal("0.00")


def test_shipping_is_charged_below_threshold() -> None:
    assert shipping_fee(Decimal("299.99")) == Decimal("12.00")


def test_member_discount_applies_at_threshold() -> None:
    assert member_discount(Decimal("100.00")) == Decimal("10.00")


def test_member_discount_is_zero_below_threshold() -> None:
    assert member_discount(Decimal("99.99")) == Decimal("0.00")


def test_member_discount_rounds_to_cents() -> None:
    assert member_discount(Decimal("123.46")) == Decimal("12.35")
