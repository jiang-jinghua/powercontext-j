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
