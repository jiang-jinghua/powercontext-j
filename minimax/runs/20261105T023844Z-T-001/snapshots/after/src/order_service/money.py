"""Exact money helpers for the order service."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(value: object) -> Decimal:
    """Parse a value into an exact Decimal amount without rounding it."""
    return Decimal(str(value))


def round_money(value: object) -> Decimal:
    """Round an amount to cents with half-up rounding (SPEC-PRC-002)."""
    return money(value).quantize(CENT, rounding=ROUND_HALF_UP)
