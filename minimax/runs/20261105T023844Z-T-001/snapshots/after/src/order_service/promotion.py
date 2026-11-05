"""Coupon registry and coupon application."""

from dataclasses import dataclass
from decimal import Decimal

from .money import money


class UnknownCouponError(ValueError):
    """Raised when a coupon code is not registered."""


class CouponNotApplicableError(ValueError):
    """Raised when a known coupon does not apply to this order."""


@dataclass(frozen=True)
class CouponRule:
    """A fixed-amount coupon judged on the goods subtotal."""

    code: str
    threshold: Decimal
    amount: Decimal

    def apply(self, goods: Decimal) -> Decimal:
        if goods < self.threshold:
            raise CouponNotApplicableError(
                f"{self.code} requires goods of at least {self.threshold}, got {goods}"
            )
        return self.amount


COUPONS: dict[str, CouponRule] = {
    "SAVE30": CouponRule(code="SAVE30", threshold=money("200"), amount=money("30")),
}


def resolve_coupon(code: str) -> CouponRule:
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
