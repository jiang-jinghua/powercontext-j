# 任务上下文包 REQ-2026-011 · 退款结算：类目排除分摊与尾差规则升级

- 生成时间：2026-11-08T22:10:44+08:00
- 规格版本：2026-11-07T20:00+08:00
- 召回证据：30 个片段

## 0. 目标文件全文

> 生成补丁必须基于下面这些真实内容构造 hunk，不要臆测行号或省略上下文。

### `src/settlement/allocation.py`（2601 字节）

```python
"""金额分摊。

规则（基线版本）：
1. 以各行应付金额为权重，按比例把待分摊总额拆到行上；
2. 比例计算使用 :class:`fractions.Fraction`，保证不引入浮点误差；
3. 每行先向下取整，剩余的分（尾差）一次性给"权重最大"的行，
   权重相同则给 line_no 最小的行。

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
        # 尾差：全部给权重最大的行（权重相同取 line_no 小者）
        target = max(rows, key=lambda r: (r[1], -r[0]))[0]
        floors[target] += remainder
    return floors


def verify_total(total_cents: int, allocated: dict[int, int]) -> None:
    """自检：分摊结果必须与待分摊金额一致。"""
    got = sum(allocated.values())
    if got != total_cents:
        raise AllocationError(
            "分摊结果与待分摊金额不一致",
            {"expected_cents": total_cents, "got_cents": got, "allocated": allocated},
        )

```

### `src/settlement/engine.py`（3624 字节）

```python
"""结算与退款主流程。"""

from __future__ import annotations

from typing import Iterable, Sequence

from .allocation import allocate, verify_total
from .errors import RefundError
from .models import (
    Coupon,
    Order,
    OrderItem,
    RefundLine,
    RefundResult,
    SettledLine,
    Settlement,
)
from .validators import validate_order, validate_return_lines


def build_order(payload: dict) -> Order:
    """从 JSON 字典构造订单（不做业务校验，校验在 engine 入口做）。"""
    items = tuple(
        OrderItem(
            line_no=int(raw["line_no"]),
            sku=str(raw["sku"]),
            category=str(raw.get("category", "default")),
            unit_price_cents=int(raw["unit_price_cents"]),
            qty=int(raw.get("qty", 1)),
        )
        for raw in payload["items"]
    )
    coupon_raw = payload.get("coupon")
    coupon = (
        Coupon(
            code=str(coupon_raw["code"]),
            type=str(coupon_raw["type"]),
            value=int(coupon_raw["value"]),
            min_spend_cents=int(coupon_raw.get("min_spend_cents", 0)),
        )
        if coupon_raw
        else None
    )
    return Order(
        order_id=str(payload["order_id"]),
        items=items,
        coupon=coupon,
        platform_subsidy_cents=int(payload.get("platform_subsidy_cents", 0)),
        shipping_fee_cents=int(payload.get("shipping_fee_cents", 0)),
    )


def settle(order: Order) -> Settlement:
    """结算整单：计算每行实付金额。

    待分摊总额 = 优惠券金额 + 平台补贴，按行应付金额比例分摊。
    """
    validate_order(order)
    gross_total = order.gross_cents

    coupon_discount = order.coupon.discount_cents(gross_total) if order.coupon else 0
    subsidy = order.platform_subsidy_cents
    to_allocate = coupon_discount + subsidy

    allocated = allocate(to_allocate, [(it.line_no, it.gross_cents) for it in order.items])
    verify_total(to_allocate, allocated)

    lines = [
        SettledLine(
            line_no=it.line_no,
            sku=it.sku,
            category=it.category,
            gross_cents=it.gross_cents,
            discount_cents=allocated[it.line_no],
            paid_cents=it.gross_cents - allocated[it.line_no],
        )
        for it in order.items
    ]
    return Settlement(
        order_id=order.order_id,
        gross_cents=gross_total,
        coupon_discount_cents=coupon_discount,
        subsidy_cents=subsidy,
        shipping_fee_cents=order.shipping_fee_cents,
        paid_total_cents=sum(ln.paid_cents for ln in lines) + order.shipping_fee_cents,
        lines=lines,
    )


def refund(
    order: Order,
    settlement: Settlement,
    returned_line_nos: Sequence[int] | Iterable[int],
) -> RefundResult:
    """按已结算结果计算部分退货的退款金额。

    规则（基线版本）：
    - 退货行的退款额 = 该行实付金额；
    - 运费仅在"整单全部退货"时退还。
    """
    returned = validate_return_lines(order, returned_line_nos)
    lines = [
        RefundLine(
            line_no=ln,
            sku=order.item(ln).sku,
            refund_cents=settlement.line(ln).paid_cents,
        )
        for ln in returned
    ]
    all_returned = set(returned) == set(order.line_nos)
    shipping = order.shipping_fee_cents if all_returned else 0
    if not lines:
        raise RefundError("退货行不能为空", {"order_id": order.order_id})
    return RefundResult(
        order_id=order.order_id,
        returned_line_nos=tuple(returned),
        lines=lines,
        shipping_refund_cents=shipping,
    )

```

### `src/settlement/validators.py`（2260 字节）

```python
"""输入与结果校验。

校验失败一律抛 ValidationError，错误码稳定可断言。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .errors import RefundError, ValidationError
from .models import Order


def validate_order(order: Order) -> None:
    """订单级校验：行号唯一、数量与单价合法、优惠券类型合法。"""
    if not order.items:
        raise ValidationError("订单没有任何行项目", {"order_id": order.order_id})

    seen: set[int] = set()
    for it in order.items:
        if it.line_no <= 0:
            raise ValidationError("line_no 必须为正整数", {"line_no": it.line_no})
        if it.line_no in seen:
            raise ValidationError("line_no 重复", {"line_no": it.line_no})
        seen.add(it.line_no)
        if it.qty <= 0:
            raise ValidationError(
                "数量必须为正整数", {"line_no": it.line_no, "qty": it.qty}
            )
        if it.unit_price_cents < 0:
            raise ValidationError(
                "单价不能为负", {"line_no": it.line_no, "unit": it.unit_price_cents}
            )
    if order.platform_subsidy_cents < 0:
        raise ValidationError(
            "平台补贴不能为负", {"subsidy": order.platform_subsidy_cents}
        )
    if order.shipping_fee_cents < 0:
        raise ValidationError("运费不能为负", {"shipping": order.shipping_fee_cents})
    if order.coupon and order.coupon.type not in ("amount", "percent"):
        raise ValidationError(
            "不支持的优惠券类型", {"type": order.coupon.type, "code": order.coupon.code}
        )


def validate_return_lines(order: Order, returned_line_nos: Sequence[int] | Iterable[int]) -> list[int]:
    """退货行校验：去重、保序、必须存在。"""
    result: list[int] = []
    for ln in returned_line_nos:
        ln = int(ln)
        if ln in result:
            continue
        if ln not in order.line_nos:
            raise RefundError(
                "退货行号不在订单中", {"line_no": ln, "order_id": order.order_id}
            )
        result.append(ln)
    if not result:
        raise RefundError("退货行不能为空", {"order_id": order.order_id})
    return result

```

### `src/settlement/errors.py`（1160 字节）

```python
"""结算引擎异常定义。

所有业务异常都带稳定的错误码（error_code），便于上游/测试断言。
错误码规范：E_ + 大写短名，写入文档 docs/errors.md 后方可新增。
"""

from __future__ import annotations


class SettlementError(Exception):
    """结算领域基础异常。

    Attributes:
        code: 稳定错误码，例如 ``E_BAD_INPUT``。
        detail: 可序列化的上下文信息，随异常一起上报。
    """

    code = "E_SETTLEMENT"

    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {"error_code": self.code, "message": self.message, "detail": self.detail}


class ValidationError(SettlementError):
    """输入校验不通过。"""

    code = "E_BAD_INPUT"


class AllocationError(SettlementError):
    """分摊计算不成立（例如权重全为 0）。"""

    code = "E_ALLOCATION"


class RefundError(SettlementError):
    """退款流程不成立（例如退货行号不存在）。"""

    code = "E_REFUND"

```

### `src/settlement/models.py`（4340 字节）

```python
"""结算领域模型。

金额字段一律是 int 分，字段名统一以 ``_cents`` 结尾，避免单位歧义。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderItem:
    """订单行项目。"""

    line_no: int
    sku: str
    category: str
    unit_price_cents: int
    qty: int

    @property
    def gross_cents(self) -> int:
        return self.unit_price_cents * self.qty


@dataclass(frozen=True)
class Coupon:
    """优惠券。当前仅支持满减（amount）与折扣（percent）两种。"""

    code: str
    type: str
    value: int
    min_spend_cents: int = 0

    def discount_cents(self, gross_cents: int) -> int:
        """按券面规则计算优惠金额（不含分摊）。"""
        if gross_cents < self.min_spend_cents:
            return 0
        if self.type == "amount":
            return min(self.value, gross_cents)
        if self.type == "percent":
            # value 表示折扣百分比，例如 88 表示打 8.8 折
            off = gross_cents * (100 - self.value) // 100
            return max(0, min(off, gross_cents))
        raise ValueError(f"unknown coupon type: {self.type}")


@dataclass(frozen=True)
class Order:
    """订单。"""

    order_id: str
    items: tuple[OrderItem, ...]
    coupon: Coupon | None = None
    platform_subsidy_cents: int = 0
    shipping_fee_cents: int = 0

    @property
    def gross_cents(self) -> int:
        return sum(i.gross_cents for i in self.items)

    def item(self, line_no: int) -> OrderItem:
        for it in self.items:
            if it.line_no == line_no:
                return it
        raise KeyError(f"line_no {line_no} not found")

    @property
    def line_nos(self) -> tuple[int, ...]:
        return tuple(i.line_no for i in self.items)


@dataclass
class SettledLine:
    """结算后的行明细。"""

    line_no: int
    sku: str
    category: str
    gross_cents: int
    discount_cents: int
    paid_cents: int


@dataclass
class Settlement:
    """整单结算结果。"""

    order_id: str
    gross_cents: int
    coupon_discount_cents: int
    subsidy_cents: int
    shipping_fee_cents: int
    paid_total_cents: int
    lines: list[SettledLine] = field(default_factory=list)

    @property
    def discount_total_cents(self) -> int:
        return self.coupon_discount_cents + self.subsidy_cents

    def line(self, line_no: int) -> SettledLine:
        for ln in self.lines:
            if ln.line_no == line_no:
                return ln
        raise KeyError(f"line_no {line_no} not found")

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "gross_cents": self.gross_cents,
            "coupon_discount_cents": self.coupon_discount_cents,
            "subsidy_cents": self.subsidy_cents,
            "shipping_fee_cents": self.shipping_fee_cents,
            "discount_total_cents": self.discount_total_cents,
            "paid_total_cents": self.paid_total_cents,
            "lines": [
                {
                    "line_no": ln.line_no,
                    "sku": ln.sku,
                    "category": ln.category,
                    "gross_cents": ln.gross_cents,
                    "discount_cents": ln.discount_cents,
                    "paid_cents": ln.paid_cents,
                }
                for ln in self.lines
            ],
        }


@dataclass
class RefundLine:
    """退款明细行。"""

    line_no: int
    sku: str
    refund_cents: int


@dataclass
class RefundResult:
    """退款结果。"""

    order_id: str
    returned_line_nos: tuple[int, ...]
    lines: list[RefundLine] = field(default_factory=list)
    shipping_refund_cents: int = 0

    @property
    def refund_total_cents(self) -> int:
        return sum(ln.refund_cents for ln in self.lines) + self.shipping_refund_cents

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "returned_line_nos": list(self.returned_line_nos),
            "lines": [
                {"line_no": ln.line_no, "sku": ln.sku, "refund_cents": ln.refund_cents}
                for ln in self.lines
            ],
            "shipping_refund_cents": self.shipping_refund_cents,
            "refund_total_cents": self.refund_total_cents,
        }

```

### `src/settlement/cli.py`（1773 字节）

```python
"""业务命令行入口。

用法：
    python -m settlement.cli settle --order tests/fixtures/order_standard.json
    python -m settlement.cli refund --order tests/fixtures/order_standard.json --return 1,2

输出统一为 JSON（stdout），错误为 {"error_code": ..., "message": ...} 且退出码 2。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import service
from .errors import SettlementError


def _parse_lines(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="settlement", description="订单退款结算引擎 CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_settle = sub.add_parser("settle", help="结算整单，输出每行实付金额")
    p_settle.add_argument("--order", required=True, help="订单 JSON 路径")

    p_refund = sub.add_parser("refund", help="计算部分退货的退款金额")
    p_refund.add_argument("--order", required=True, help="订单 JSON 路径")
    p_refund.add_argument("--return", dest="returned", required=True, help="退货行号，逗号分隔")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "settle":
            result = service.settle_file(args.order)
        else:
            result = service.refund_file(args.order, _parse_lines(args.returned))
    except SettlementError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

```

## 1. 规格条目

### SPEC-011-01 · REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-01 优惠分摊支持类目排除
- 来源：`spec/REQ-2026-011.md`（版本 2026-11-07T20:00+08:00）
- 片段：`REQ-2026-011#6-d1c897e446b5`

```text
REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-01 优惠分摊支持类目排除
### SPEC-011-01 优惠分摊支持类目排除

- 优惠券定义新增可选字段 `excluded_categories: list[str]`（缺省 `[]`）。
- 分摊基数的构成：`gross > 0 且 category ∉ excluded_categories` 的行。
- 被排除行的分摊额恒为 `0`，其实付等于行应付；原份额按剩余行基数比例重新分摊。
- 整单口径不变：所有行分摊额之和仍等于待分摊额，整单实付 = `gross_total − 待分摊额`。
- 边界：若全部行都被排除（或剩余基数合计为 0），则待分摊额不向任何行分摊，不得抛异常；
  此时待分摊额记为「整单未分摊优惠」`unallocated_discount_cents`，整单实付仍按
  `paid_total = Σ行实付 + 运费 − 未分摊优惠` 计算，保证整单口径不变。
- 平台补贴与优惠券共用同一份 `excluded_categories`。
```

### SPEC-011-02 · REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-02 尾差按行轮转分配
- 来源：`spec/REQ-2026-011.md`（版本 2026-11-07T20:00+08:00）
- 片段：`REQ-2026-011#7-445fb4bbca8e`

```text
REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-02 尾差按行轮转分配
### SPEC-011-02 尾差按行轮转分配

- 每行先按 `Fraction(total × basis_i, Σbasis)` 向下取整。
- 尾差按「1 分/行」逐行分配；顺序为：该行分摊基数降序，基数相同则 `line_no` 升序。
- 单行尾差补偿上限为 1 分；尾差分配完毕后，各行分摊额之和必须精确等于待分摊额。
- 该策略替代基线版本的「尾差全部分给单行」，不得保留旧分支。
```

### SPEC-011-03 · REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-03 运费退还判定改为「剩余实付为 0」
- 来源：`spec/REQ-2026-011.md`（版本 2026-11-07T20:00+08:00）
- 片段：`REQ-2026-011#8-0e225a95c429`

```text
REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-03 运费退还判定改为「剩余实付为 0」
### SPEC-011-03 运费退还判定改为「剩余实付为 0」

- 退货后计算剩余实付 = 未退货行的 `paid` 合计。
- 剩余实付为 0 时退还全额运费；否则运费不退。
- 全部退货必然满足该条件，故与基线"整单全退才退运费"向后兼容。
- 运费退还额只计入 `shipping_refund_cents`，不摊入任何行。
```

### SPEC-011-04 · REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-04 负金额校验
- 来源：`spec/REQ-2026-011.md`（版本 2026-11-07T20:00+08:00）
- 片段：`REQ-2026-011#9-8103a4172129`

```text
REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-04 负金额校验
### SPEC-011-04 负金额校验

- 分摊完成后，若存在 `paid < 0` 的行，抛 `NegativeAllocationError`，`error_code = E_NEG_ALLOC`，
  `detail` 至少包含 `order_id`、`line_no`、`paid_cents`。
- 退款明细计算完成后，若存在 `refund < 0`，抛 `NegativeRefundError`，`error_code = E_NEG_REFUND`。
- 两个异常均继承自 `RefundAmountError`（再继承 `SettlementError`）。
- **禁止**用 `max(0, x)` 静默夹断负值：负值代表数据结构或规则冲突，必须显式失败。
```

### SPEC-011-05 · REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-05 退款预览接口
- 来源：`spec/REQ-2026-011.md`（版本 2026-11-07T20:00+08:00）
- 片段：`REQ-2026-011#10-9e68a28b4d55`

```text
REQ-2026-011 退款结算：类目排除分摊与尾差规则升级 / 4. 规格条目 / SPEC-011-05 退款预览接口
### SPEC-011-05 退款预览接口

- 新增 `engine.preview_refund(order, returned_line_nos) -> RefundResult`：内部完成结算 + 退款计算，
  调用方无需先持有 `Settlement`。
- 新增 `service.preview_refund_file(path, returned_line_nos) -> dict`。
- 新增 CLI 子命令：`python -m settlement.cli preview --order <path> --return 1,2`，输出 JSON 与 `refund` 子命令同构。
- 预览为只读操作：不写库、不改订单状态、不产生副作用。
- 预览结果必须与"先 `settle` 再 `refund`"的结果逐字段一致。
```

## 2. 相关代码

### `src/settlement/allocation.py` :: `allocation`
- 片段：`allocation#0-ca1bef1c6ca8`（score=4.2588）

```python
"""金额分摊。

规则（基线版本）：
1. 以各行应付金额为权重，按比例把待分摊总额拆到行上；
2. 比例计算使用 :class:`fractions.Fraction`，保证不引入浮点误差；
3. 每行先向下取整，剩余的分（尾差）一次性给"权重最大"的行，
   权重相同则给 line_no 最小的行。

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
```

### `src/settlement/allocation.py` :: `allocate`
- 片段：`allocation#2-2bd64bd8ec14`（score=3.6）

```python
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
        # 尾差：全部给权重最大的行（权重相同取 line_no 小者）
        target = max(rows, key=lambda r: (r[1], -r[0]))[0]
        floors[target] += remainder
    return floors
```

### `src/settlement/money.py` :: `money`
- 片段：`money#0-d0b6952f95c3`（score=3.5275）

```python
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
```

### `src/settlement/models.py` :: `Coupon`
- 片段：`models#3-4d5e5f667bc5`（score=3.5235）

```python
class Coupon:
    """优惠券。当前仅支持满减（amount）与折扣（percent）两种。"""

    code: str
    type: str
    value: int
    min_spend_cents: int = 0

    def discount_cents(self, gross_cents: int) -> int:
        """按券面规则计算优惠金额（不含分摊）。"""
        if gross_cents < self.min_spend_cents:
            return 0
        if self.type == "amount":
            return min(self.value, gross_cents)
        if self.type == "percent":
            # value 表示折扣百分比，例如 88 表示打 8.8 折
            off = gross_cents * (100 - self.value) // 100
            return max(0, min(off, gross_cents))
        raise ValueError(f"unknown coupon type: {self.type}")
```

### `src/settlement/validators.py` :: `validate_order`
- 片段：`validators#1-93599e3d5991`（score=3.4647）

```python
def validate_order(order: Order) -> None:
    """订单级校验：行号唯一、数量与单价合法、优惠券类型合法。"""
    if not order.items:
        raise ValidationError("订单没有任何行项目", {"order_id": order.order_id})

    seen: set[int] = set()
    for it in order.items:
        if it.line_no <= 0:
            raise ValidationError("line_no 必须为正整数", {"line_no": it.line_no})
        if it.line_no in seen:
            raise ValidationError("line_no 重复", {"line_no": it.line_no})
        seen.add(it.line_no)
        if it.qty <= 0:
            raise ValidationError(
                "数量必须为正整数", {"line_no": it.line_no, "qty": it.qty}
            )
        if it.unit_price_cents < 0:
            raise ValidationError(
                "单价不能为负", {"line_no": it.line_no, "unit": it.unit_price_cents}
            )
    if order.platform_subsidy_cents < 0:
        raise ValidationError(
            "平台补贴不能为负", {"subsidy": order.platform_subsidy_cents}
        )
    if order.shipping_fee_cents < 0:
        raise ValidationError("运费不能为负", {"shipping": order.shipping_fee_cents})
    if order.coupon and order.coupon.type not in ("amount", "percent"):
        raise ValidationError(
            "不支持的优惠券类型", {"type": order.coupon.type, "code": order.coupon.code}
        )
```

### `src/settlement/engine.py` :: `refund`
- 片段：`engine#3-e0482a733338`（score=3.4412）

```python
def refund(
    order: Order,
    settlement: Settlement,
    returned_line_nos: Sequence[int] | Iterable[int],
) -> RefundResult:
    """按已结算结果计算部分退货的退款金额。

    规则（基线版本）：
    - 退货行的退款额 = 该行实付金额；
    - 运费仅在"整单全部退货"时退还。
    """
    returned = validate_return_lines(order, returned_line_nos)
    lines = [
        RefundLine(
            line_no=ln,
            sku=order.item(ln).sku,
            refund_cents=settlement.line(ln).paid_cents,
        )
        for ln in returned
    ]
    all_returned = set(returned) == set(order.line_nos)
    shipping = order.shipping_fee_cents if all_returned else 0
    if not lines:
        raise RefundError("退货行不能为空", {"order_id": order.order_id})
    return RefundResult(
        order_id=order.order_id,
        returned_line_nos=tuple(returned),
        lines=lines,
        shipping_refund_cents=shipping,
    )
```

### `src/settlement/__init__.py` :: `__init__`
- 片段：`__init__#0-67a77019c70b`（score=3.4392）

```python
"""订单退款结算引擎。

模块职责：
- models    领域模型（订单 / 行项目 / 优惠券 / 结算结果）
- allocation 金额分摊（按权重比例 + 尾差处理）
- engine    结算与退款预览主流程
- validators 输入与结果校验
- service   面向调用方的服务层（加载 JSON、输出可序列化结果）
- cli       命令行入口（settle / refund）
"""

__all__ = ["__version__"]
__version__ = "1.4.0"
```

### `src/settlement/cli.py` :: `cli`
- 片段：`cli#0-c9dc735f67e8`（score=3.3059）

```python
"""业务命令行入口。

用法：
    python -m settlement.cli settle --order tests/fixtures/order_standard.json
    python -m settlement.cli refund --order tests/fixtures/order_standard.json --return 1,2

输出统一为 JSON（stdout），错误为 {"error_code": ..., "message": ...} 且退出码 2。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import service
from .errors import SettlementError


def _parse_lines(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
```

### `src/settlement/allocation.py` :: `normalize_basis`
- 片段：`allocation#1-61a8afd43c7e`（score=3.2647）

```python
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
```

### `src/settlement/models.py` :: `Coupon.discount_cents`
- 片段：`models#4-50431fa407d8`（score=2.9647）

```python
def discount_cents(self, gross_cents: int) -> int:
        """按券面规则计算优惠金额（不含分摊）。"""
        if gross_cents < self.min_spend_cents:
            return 0
        if self.type == "amount":
            return min(self.value, gross_cents)
        if self.type == "percent":
            # value 表示折扣百分比，例如 88 表示打 8.8 折
            off = gross_cents * (100 - self.value) // 100
            return max(0, min(off, gross_cents))
        raise ValueError(f"unknown coupon type: {self.type}")
```

### `src/settlement/cli.py` :: `build_parser`
- 片段：`cli#2-138f3ad81c81`（score=2.9471）

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="settlement", description="订单退款结算引擎 CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_settle = sub.add_parser("settle", help="结算整单，输出每行实付金额")
    p_settle.add_argument("--order", required=True, help="订单 JSON 路径")

    p_refund = sub.add_parser("refund", help="计算部分退货的退款金额")
    p_refund.add_argument("--order", required=True, help="订单 JSON 路径")
    p_refund.add_argument("--return", dest="returned", required=True, help="退货行号，逗号分隔")

    return parser
```

### `src/settlement/engine.py` :: `settle`
- 片段：`engine#2-c4f34cc72e74`（score=2.9235）

```python
def settle(order: Order) -> Settlement:
    """结算整单：计算每行实付金额。

    待分摊总额 = 优惠券金额 + 平台补贴，按行应付金额比例分摊。
    """
    validate_order(order)
    gross_total = order.gross_cents

    coupon_discount = order.coupon.discount_cents(gross_total) if order.coupon else 0
    subsidy = order.platform_subsidy_cents
    to_allocate = coupon_discount + subsidy

    allocated = allocate(to_allocate, [(it.line_no, it.gross_cents) for it in order.items])
    verify_total(to_allocate, allocated)

    lines = [
        SettledLine(
            line_no=it.line_no,
            sku=it.sku,
            category=it.category,
            gross_cents=it.gross_cents,
            discount_cents=allocated[it.line_no],
            paid_cents=it.gross_cents - allocated[it.line_no],
        )
        for it in order.items
    ]
    return Settlement(
        order_id=order.order_id,
        gross_cents=gross_total,
        coupon_discount_cents=coupon_discount,
        subsidy_cents=subsidy,
        shipping_fee_cents=order.shipping_fee_cents,
        paid_total_cents=sum(ln.paid_cents for ln in lines) + order.shipping_fee_cents,
        lines=lines,
    )
```

### `src/settlement/allocation.py` :: `verify_total`
- 片段：`allocation#3-8fd43d571bd4`（score=1.9818）

```python
def verify_total(total_cents: int, allocated: dict[int, int]) -> None:
    """自检：分摊结果必须与待分摊金额一致。"""
    got = sum(allocated.values())
    if got != total_cents:
        raise AllocationError(
            "分摊结果与待分摊金额不一致",
            {"expected_cents": total_cents, "got_cents": got, "allocated": allocated},
        )
```

### `src/settlement/errors.py` :: `errors`
- 片段：`errors#0-2ad2bc63c949`（score=1.2091）

```python
"""结算引擎异常定义。

所有业务异常都带稳定的错误码（error_code），便于上游/测试断言。
错误码规范：E_ + 大写短名，写入文档 docs/errors.md 后方可新增。
"""

from __future__ import annotations


class SettlementError(Exception):
    """结算领域基础异常。

    Attributes:
        code: 稳定错误码，例如 ``E_BAD_INPUT``。
        detail: 可序列化的上下文信息，随异常一起上报。
    """

    code = "E_SETTLEMENT"

    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
```

### `src/settlement/validators.py` :: `validators`
- 片段：`validators#0-8bcd149732ee`（score=0.8364）

```python
"""输入与结果校验。

校验失败一律抛 ValidationError，错误码稳定可断言。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .errors import RefundError, ValidationError
from .models import Order


def validate_order(order: Order) -> None:
    """订单级校验：行号唯一、数量与单价合法、优惠券类型合法。"""
    if not order.items:
        raise ValidationError("订单没有任何行项目", {"order_id": order.order_id})

    seen: set[int] = set()
    for it in order.items:
        if it.line_no <= 0:
            raise ValidationError("line_no 必须为正整数", {"line_no": it.line_no})
        if it.line_no in seen:
            raise ValidationError("line_no 重复", {"line_no": it.line_no})
        seen.add(it.line_no)
```

## 3. 验证测试

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_01_excluded_category_gets_no_allocation`
- 片段：`test_req_2026_011#3-d2ee1ae25eee`（score=4.3128）

```python
def test_spec_011_01_excluded_category_gets_no_allocation():
    """SPEC-011-01：被排除类目分摊额为 0，份额按剩余行基数重分配，整单口径不变。"""
    result = settle(load("order_excluded_gift.json"))
    got = [(ln.discount_cents, ln.paid_cents) for ln in result.lines]
    assert got == [(10417, 89583), (2083, 17917), (0, 5000)]
    assert result.paid_total_cents == 112500 + 1200
    assert result.unallocated_discount_cents == 0
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_01_all_categories_excluded_keeps_gross`
- 片段：`test_req_2026_011#4-05283d09787a`（score=4.1851）

```python
def test_spec_011_01_all_categories_excluded_keeps_gross():
    """SPEC-011-01 边界：全部行被排除时不抛异常，待分摊额记为整单未分摊优惠。"""
    payload = raw("order_excluded_gift.json")
    payload["coupon"]["excluded_categories"] = ["digital", "book", "gift"]
    result = settle(build_order(payload))
    assert [ln.discount_cents for ln in result.lines] == [0, 0, 0]
    assert [ln.paid_cents for ln in result.lines] == [100000, 20000, 5000]
    assert result.unallocated_discount_cents == 10000 + 2500
    assert result.paid_total_cents == 125000 - 12500 + 1200
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_01_percent_coupon_respects_exclusions`
- 片段：`test_req_2026_011#5-e004a99df535`（score=3.8213）

```python
def test_spec_011_01_percent_coupon_respects_exclusions():
    """SPEC-011-01：折扣券（percent）与满减共用同一套类目排除语义。"""
    payload = raw("order_excluded_gift.json")
    payload["coupon"] = {
        "code": "CPN_PCT",
        "type": "percent",
        "value": 88,
        "min_spend_cents": 0,
        "excluded_categories": ["gift"],
    }
    result = settle(build_order(payload))
    assert result.coupon_discount_cents == 15000
    # 待分摊额 = 券 15000 + 平台补贴 2500 = 17500，全部落在未排除的两行上
    assert [(ln.discount_cents, ln.paid_cents) for ln in result.lines] == [
        (14584, 85416),
        (2916, 17084),
        (0, 5000),
    ]
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_03_shipping_refunded_when_remaining_paid_is_zero`
- 片段：`test_req_2026_011#8-3779bffc852b`（score=3.7851）

```python
def test_spec_011_03_shipping_refunded_when_remaining_paid_is_zero():
    """SPEC-011-03：退货后剩余实付为 0 时退还全额运费。"""
    order = load("order_fullpay.json")
    settlement = settle(order)
    assert [ln.paid_cents for ln in settlement.lines] == [0, 0, 0]
    result = refund(order, settlement, [1, 2])
    assert result.shipping_refund_cents == 1200
    assert result.refund_total_cents == 1200
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_04_negative_allocation_raises`
- 片段：`test_req_2026_011#10-6942f591b5fd`（score=3.7489）

```python
def test_spec_011_04_negative_allocation_raises():
    """SPEC-011-04：分摊后行实付为负必须显式失败（E_NEG_ALLOC），不得静默夹断。"""
    with pytest.raises(NegativeAllocationError) as exc:
        settle(load("order_negative.json"))
    assert exc.value.code == "E_NEG_ALLOC"
    assert exc.value.detail["line_no"] == 2
    assert exc.value.detail["paid_cents"] == -30000
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_05_preview_service_and_cli`
- 片段：`test_req_2026_011#13-69526c5bae49`（score=3.6787）

```python
def test_spec_011_05_preview_service_and_cli():
    """SPEC-011-05：服务层与 CLI 都提供预览入口，且只读无副作用。"""
    assert (
        service.preview_refund_file(FIXTURES / "order_excluded_gift.json", [3])["refund_total_cents"]
        == 5000
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "settlement.cli",
            "preview",
            "--order",
            str(FIXTURES / "order_excluded_gift.json"),
            "--return",
            "1,3",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["refund_total_cents"] == 89583 + 5000
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_02_tail_order_fixture`
- 片段：`test_req_2026_011#7-1f836dffaf82`（score=3.6574）

```python
def test_spec_011_02_tail_order_fixture():
    """SPEC-011-02：端到端验证尾差轮转后的行实付，行和等于整单实付。"""
    result = settle(load("order_tail.json"))
    assert [ln.discount_cents for ln in result.lines] == [3334, 3334, 3333]
    assert [ln.paid_cents for ln in result.lines] == [6666, 6666, 6667]
    assert result.paid_total_cents == 30000 - 10001
```

### `tests/acceptance/test_req_2026_012.py` :: `test_spec_011_06_cli_output_contains_field`
- 片段：`test_req_2026_012#5-55e39e2d5440`（score=3.4936）

```python
def test_spec_011_06_cli_output_contains_field():
    """SPEC-011-06：CLI 输出包含 remaining_paid_cents，且不影响退款总额。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "settlement.cli",
            "refund",
            "--order",
            str(FIXTURES / "order_standard.json"),
            "--return",
            "1,2",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["remaining_paid_cents"] == 4500
    assert payload["refund_total_cents"] == 90000 + 18000
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_04_negative_refund_raises`
- 片段：`test_req_2026_011#11-43c442991e1c`（score=2.4444）

```python
def test_spec_011_04_negative_refund_raises():
    """SPEC-011-04：退款明细为负必须抛 E_NEG_REFUND。"""
    with pytest.raises(NegativeRefundError) as exc:
        validate_refund_amounts("SO1", [RefundLine(line_no=1, sku="SKU-A", refund_cents=-1)])
    assert exc.value.code == "E_NEG_REFUND"
```

### `tests/acceptance/test_req_2026_011.py` :: `test_spec_011_03_shipping_not_refunded_when_remaining_paid_left`
- 片段：`test_req_2026_011#9-65396fce96ec`（score=2.2444）

```python
def test_spec_011_03_shipping_not_refunded_when_remaining_paid_left():
    """SPEC-011-03：仍有剩余实付时不退运费（与基线行为一致）。"""
    order = load("order_standard.json")
    result = refund(order, settle(order), [1, 2])
    assert result.shipping_refund_cents == 0
```

## 4. 任务状态

- 已完成：无
- 待办：SPEC-011-06（规格 v2 追加）：退款结果增加剩余实付字段
- 已知遗留：BACKLOG-311 多次部分退货的累积对账未覆盖; BACKLOG-312 折扣券 + 类目排除缺少专项用例

## 5. 检索留痕

- [spec] `退款结算：类目排除分摊与尾差规则升级 按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → REQ-2026-011#2-17c83a3ac603, REQ-2026-011#6-d1c897e446b5, REQ-2026-011#3-9d5c04625a3b
- [spec] `SPEC-011-01` → REQ-2026-011#6-d1c897e446b5, REQ-2026-011#8-0e225a95c429, REQ-2026-011#7-445fb4bbca8e
- [spec] `SPEC-011-02` → REQ-2026-011#7-445fb4bbca8e, REQ-2026-011#8-0e225a95c429, REQ-2026-011#10-9e68a28b4d55
- [spec] `SPEC-011-03` → REQ-2026-011#8-0e225a95c429, REQ-2026-011#7-445fb4bbca8e, REQ-2026-011#10-9e68a28b4d55
- [spec] `SPEC-011-04` → REQ-2026-011#9-8103a4172129, REQ-2026-011#8-0e225a95c429, REQ-2026-011#7-445fb4bbca8e
- [spec] `SPEC-011-05` → REQ-2026-011#10-9e68a28b4d55, REQ-2026-011#8-0e225a95c429, REQ-2026-011#7-445fb4bbca8e
- [code] `allocation 退款结算：类目排除分摊与尾差规则升级` → allocation#0-ca1bef1c6ca8, __init__#0-67a77019c70b, allocation#3-8fd43d571bd4
- [code] `src/settlement/allocation.py 按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → allocation#0-ca1bef1c6ca8, allocation#2-2bd64bd8ec14, __init__#0-67a77019c70b
- [code] `engine 退款结算：类目排除分摊与尾差规则升级` → __init__#0-67a77019c70b, allocation#0-ca1bef1c6ca8, engine#3-e0482a733338
- [code] `src/settlement/engine.py 按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → allocation#0-ca1bef1c6ca8, __init__#0-67a77019c70b, money#0-d0b6952f95c3
- [code] `validators 退款结算：类目排除分摊与尾差规则升级` → __init__#0-67a77019c70b, allocation#0-ca1bef1c6ca8, engine#3-e0482a733338
- [code] `src/settlement/validators.py 按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → allocation#0-ca1bef1c6ca8, __init__#0-67a77019c70b, money#0-d0b6952f95c3
- [code] `errors 退款结算：类目排除分摊与尾差规则升级` → allocation#0-ca1bef1c6ca8, __init__#0-67a77019c70b, engine#3-e0482a733338
- [code] `src/settlement/errors.py 按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → allocation#0-ca1bef1c6ca8, money#0-d0b6952f95c3, models#3-4d5e5f667bc5
- [code] `models 退款结算：类目排除分摊与尾差规则升级` → __init__#0-67a77019c70b, allocation#0-ca1bef1c6ca8, engine#3-e0482a733338
- [code] `src/settlement/models.py 按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → allocation#0-ca1bef1c6ca8, __init__#0-67a77019c70b, money#0-d0b6952f95c3
- [code] `cli 退款结算：类目排除分摊与尾差规则升级` → __init__#0-67a77019c70b, allocation#0-ca1bef1c6ca8, engine#3-e0482a733338
- [code] `src/settlement/cli.py 按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → allocation#0-ca1bef1c6ca8, __init__#0-67a77019c70b, money#0-d0b6952f95c3
- [test] `SPEC-011-01 SPEC-011-02 SPEC-011-03 SPEC-011-04 SPEC-011-05 退款结算：类目排除分摊与尾差规则升级` → test_req_2026_011#5-e004a99df535, test_req_2026_011#3-d2ee1ae25eee, test_req_2026_011#13-69526c5bae49
- [test] `按 spec/REQ-2026-011.md 实现 SPEC-011-01 ~ SPEC-011-05：优惠分摊支持类目排除、尾差改为逐行 1 分轮转、运费按“剩余实付为 0”退还、补齐负金额校验、新增退款预览接口。回归用例不得修改。` → test_req_2026_011#3-d2ee1ae25eee, test_req_2026_011#4-05283d09787a, test_req_2026_011#5-e004a99df535
