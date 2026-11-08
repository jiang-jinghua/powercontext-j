"""金额工具：内部统一使用“分”（int），禁止浮点参与结算。

对外展示时才转成人民币字符串。浮点只在分摊比例计算中出现，
且必须经由 :class:`fractions.Fraction` 精确化。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def yuan_to_cents(text: str | int | float | Decimal) -> int:
    """把元金额（如 "1299.00"）转成分，四舍五入到分。"""
    value = Decimal(str(text))
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_yuan(cents: int) -> str:
    """把分转成两位小数的元字符串。"""
    return f"{Decimal(cents) / Decimal(100):.2f}"


def sum_cents(values) -> int:
    """对一组分求和，保持 int 语义。"""
    total = 0
    for v in values:
        if not isinstance(v, int) or isinstance(v, bool):
            raise TypeError(f"金额必须是 int 分，收到 {v!r}")
        total += int(v)
    return total
