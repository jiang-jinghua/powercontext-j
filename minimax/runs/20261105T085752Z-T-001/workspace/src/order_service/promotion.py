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
