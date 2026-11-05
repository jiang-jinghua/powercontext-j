### FILE: src/order_service/money.py
```python
"""Exact money helpers for the order service."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(value: object) -> Decimal:
    """Parse a value into an exact Decimal amount without rounding it."""
    return Decimal(str(value))


def round_money(value: object) -> Decimal:
    """Round an amount to cents using half-up rounding."""
    return money(value).quantize(CENT, rounding=ROUND_HALF_UP)
```

### FILE: src/order_service/promotion.py
```python
"""Coupon registry and coupon application."""

from decimal import Decimal

from .money import money


class UnknownCouponError(ValueError):
    """Raised when a coupon code is not registered."""


class CouponNotApplicableError(ValueError):
    """Raised when a known coupon does not apply to this order."""


class Save30Rule:
    """Apply the SAVE30 coupon to a qualifying goods amount."""

    threshold = money("200")
    discount = money("30")

    def apply(self, amount: Decimal) -> Decimal:
        """Return the SAVE30 discount, or reject a below-threshold amount."""
        if amount < self.threshold:
            raise CouponNotApplicableError(
                "SAVE30 requires a goods subtotal of at least 200.00"
            )
        return self.discount


COUPONS: dict[str, object] = {
    "SAVE30": Save30Rule(),
}


def resolve_coupon(code: str) -> object:
    """Look up a coupon rule by code."""
    try:
        return COUPONS[code]
    except KeyError as exc:
        raise UnknownCouponError(f"unknown coupon: {code}") from exc


def apply_coupon(amount: Decimal, code: str) -> Decimal:
    """Return the discount for ``code`` on the goods amount."""
    rule = resolve_coupon(code)
    return rule.apply(amount)


def no_discount() -> Decimal:
    """A convenience zero used by callers that do not apply a coupon."""
    return money("0")
```

### FILE: src/order_service/service.py
```python
"""Quote a shopping cart: goods, member discount, coupon and shipping."""

from dataclasses import dataclass
from decimal import Decimal

from .money import money, round_money
from .pricing import LineItem, member_discount, shipping_fee, subtotal
from .promotion import apply_coupon


@dataclass(frozen=True)
class Quote:
    """The priced order as shown to the customer."""

    subtotal: Decimal
    member_discount: Decimal
    coupon_discount: Decimal
    shipping: Decimal
    total: Decimal


def quote(items: object, coupon_code: str | None = None) -> Quote:
    """Price ``items`` with the optional coupon and the member discount."""
    lines = tuple(items)
    if not lines:
        raise ValueError("order requires at least one item")

    goods = subtotal(lines)
    member = member_discount(goods)
    coupon = money("0")
    if coupon_code is not None:
        coupon = apply_coupon(goods, coupon_code)

    discounted_goods = goods - member - coupon
    if discounted_goods < 0:
        discounted_goods = money("0")
    shipping = shipping_fee(goods)
    return Quote(
        subtotal=goods,
        member_discount=member,
        coupon_discount=coupon,
        shipping=shipping,
        total=round_money(discounted_goods + shipping),
    )
```