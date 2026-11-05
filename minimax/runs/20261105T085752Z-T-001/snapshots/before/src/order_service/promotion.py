"""Coupon registry and coupon application."""

from decimal import Decimal

from .money import money


class UnknownCouponError(ValueError):
    """Raised when a coupon code is not registered."""


class CouponNotApplicableError(ValueError):
    """Raised when a known coupon does not apply to this order."""


COUPONS: dict[str, object] = {}


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
