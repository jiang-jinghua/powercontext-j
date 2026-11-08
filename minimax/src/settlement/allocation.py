"""金额分摊。

规则：
1. 以各行应付金额为权重，按比例把待分摊总额拆到行上；
2. 比例计算使用 :class:`fractions.Fraction`，保证不引入浮点误差；
3. 每行先向下取整，剩余的分按基数降序、line_no 升序逐行轮转分配。

该模块被 engine.settle 复用，退款金额完全由这里的分摊结果推导，
因此分摊正确性直接决定退款正确性。
"""

from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Sequence

from .errors import AllocationError


def normalize_basis(basis: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """校验并规范化分摊基数：[(line_no, weight_cents)]。"""
    rows: list[tuple[int, int]] = []
    for line_no, weight in basis:
        if weight < 0:
            raise AllocationError(
                "分摊基数不能为负", {"line_no": line_no, "weight_cents": weight}
            )
        rows.append((int(line_no), int(weight)))
    if not rows:
        raise AllocationError("分摊基数为空")
    if sum(w for _, w in rows) == 0:
        raise AllocationError(
            "分摊基数总和为 0，无法按比例分摊",
            {"line_nos": [ln for ln, _ in rows]},
        )
    return rows


def allocate(total_cents: int, basis: Sequence[tuple[int, int]]) -> dict[int, int]:
    """把 ``total_cents`` 按 ``basis`` 权重分摊，返回 {line_no: 分摊额}。

    返回值的和严格等于 ``total_cents``。
    """
    if total_cents < 0:
        raise AllocationError("待分摊金额不能为负", {"total_cents": total_cents})
    rows = normalize_basis(basis)
    weight_sum = sum(w for _, w in rows)

    floors: dict[int, int] = {}
    for line_no, weight in rows:
        exact = Fraction(total_cents * weight, weight_sum)
        floors[line_no] = exact.numerator // exact.denominator

    remainder = total_cents - sum(floors.values())
    if remainder:
        ordered = sorted(rows, key=lambda row: (-row[1], row[0]))
        for line_no, _ in ordered:
            if not remainder:
                break
            floors[line_no] += 1
            remainder -= 1
    return floors


def verify_total(total_cents: int, allocated: dict[int, int]) -> None:
    """自检：分摊结果必须与待分摊金额一致。"""
    got = sum(allocated.values())
    if got != total_cents:
        raise AllocationError(
            "分摊结果与待分摊金额不一致",
            {"expected_cents": total_cents, "got_cents": got, "allocated": allocated},
        )
