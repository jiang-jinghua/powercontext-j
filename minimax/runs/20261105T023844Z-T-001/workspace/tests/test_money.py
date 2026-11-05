from decimal import Decimal

from order_service.money import money, round_money


def test_money_parses_exact_decimal() -> None:
    assert money("19.99") == Decimal("19.99")


def test_round_money_keeps_cents() -> None:
    assert round_money("19.994") == Decimal("19.99")


def test_round_money_handles_whole_numbers() -> None:
    assert round_money(12) == Decimal("12.00")
