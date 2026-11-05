"""Line items, subtotal, shipping and member discount."""

from dataclasses import dataclass
from decimal import Decimal

from .money import money, round_money

FREE_SHIPPING_THRESHOLD = money("300")
SHIPPING_FEE = money("12")
MEMBER_DISCOUNT_THRESHOLD = money("100")
MEMBER_DISCOUNT_RATE = money("0.10")


@dataclass(frozen=True)
class LineItem:
    """One order line. The amount is unit price times quantity."""

    sku: str
    unit_price: Decimal
    quantity: int = 1

    def __post_init__(self) -> None:
        if not self.sku:
            raise ValueError("sku must not be empty")
        if self.quantity < 1:
            raise ValueError("quantity must be positive")
        if self.unit_price < 0:
            raise ValueError("unit_price must not be negative")

    @property
    def amount(self) -> Decimal:
        return round_money(self.unit_price * self.quantity)


def subtotal(items: object) -> Decimal:
    """Sum line amounts. Shipping and discounts are not part of the subtotal."""
    return round_money(sum((item.amount for item in items), Decimal("0")))


def shipping_fee(amount: Decimal) -> Decimal:
    """Charge shipping below the free-shipping threshold, judged on goods only."""
    return money("0") if amount >= FREE_SHIPPING_THRESHOLD else SHIPPING_FEE


def member_discount(amount: Decimal) -> Decimal:
    """Return the 10% member discount for orders that reach the threshold."""
    if amount < MEMBER_DISCOUNT_THRESHOLD:
        return money("0")
    return round_money(amount * MEMBER_DISCOUNT_RATE)
