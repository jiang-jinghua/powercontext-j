"""分摊算法回归测试（基线行为）。"""

from __future__ import annotations

import pytest

from settlement.allocation import allocate, verify_total
from settlement.errors import AllocationError


def test_allocate_sums_to_total_with_zero_remainder():
    result = allocate(12500, [(1, 100000), (2, 20000), (3, 5000)])
    assert result == {1: 10000, 2: 2000, 3: 500}
    verify_total(12500, result)


def test_allocate_remainder_single_yuan_goes_to_largest_weight():
    # 10000 / 3 -> 每行 3333 分，尾差 1 分给基数最大行（三者相同，取 line_no 最小）
    result = allocate(10000, [(1, 1), (2, 1), (3, 1)])
    assert result == {1: 3334, 2: 3333, 3: 3333}
    assert sum(result.values()) == 10000


def test_allocate_never_loses_cent():
    for total in (0, 1, 7, 999, 13333, 128567):
        for basis in ([(1, 3), (2, 5)], [(1, 1), (2, 1), (3, 1)], [(1, 100), (2, 1)]):
            result = allocate(total, basis)
            assert sum(result.values()) == total


def test_allocate_zero_total_is_all_zero():
    assert allocate(0, [(1, 5), (2, 5)]) == {1: 0, 2: 0}


def test_allocate_rejects_zero_weight_basis():
    with pytest.raises(AllocationError) as exc:
        allocate(100, [(1, 0), (2, 0)])
    assert exc.value.code == "E_ALLOCATION"


def test_allocate_rejects_negative_total():
    with pytest.raises(AllocationError):
        allocate(-1, [(1, 10)])
