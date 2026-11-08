```file path=src/settlement/allocation.py
"""金额分摊。

规则：
1. 以各行应付金额为权重，按比例把待分摊总额拆到行上；
2. 比例计算使用 :class:`fractions.Fraction`，保证不引入浮点误差；
3. 每行先向下取整，剩余的分按权重顺序逐行分配，每行最多补偿 1 分。
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

    返回值的和严格等于 ``total_cents``。尾差按权重降序、line_no 升序
    的顺序逐行分配，每行最多分配 1 分。
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
        ordered_rows = sorted(rows, key=lambda row: (-row[1], row[0]))
        index = 0
        while remainder > 0:
            line_no, _ = ordered_rows[index % len(ordered_rows)]
            floors[line_no] += 1
            remainder -= 1
            index += 1
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

```file path=src/settlement/errors.py
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


class RefundAmountError(SettlementError):
    """退款金额计算结果不合法。"""

    code = "E_REFUND_AMOUNT"


class NegativeAllocationError(RefundAmountError):
    """分摊后行实付金额为负。"""

    code = "E_NEG_ALLOC"


class NegativeRefundError(RefundAmountError):
    """退款明细金额为负。"""

    code = "E_NEG_REFUND"
```

```file path=src/settlement/models.py
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
    excluded_categories: list[str] = field(default_factory=list)

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
    unallocated_discount_cents: int = 0

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
            "unallocated_discount_cents": self.unallocated_discount_cents,
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
    remaining_paid_cents: int = 0

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
            "remaining_paid_cents": self.remaining_paid_cents,
            "refund_total_cents": self.refund_total_cents,
        }
```

```file path=src/settlement/validators.py
"""输入与结果校验。

校验失败一律抛 ValidationError，错误码稳定可断言。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .errors import NegativeRefundError, RefundError, ValidationError
from .models import Order, RefundLine


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


def validate_return_lines(
    order: Order, returned_line_nos: Sequence[int] | Iterable[int]
) -> list[int]:
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


def validate_refund_amounts(order_id: str, lines: Sequence[RefundLine]) -> None:
    """校验退款明细不存在负金额。"""
    for line in lines:
        if line.refund_cents < 0:
            raise NegativeRefundError(
                "退款金额不能为负",
                {
                    "order_id": order_id,
                    "line_no": line.line_no,
                    "refund_cents": line.refund_cents,
                },
            )
```

```file path=src/settlement/engine.py
"""结算与退款主流程。"""

from __future__ import annotations

from typing import Iterable, Sequence

from .allocation import allocate, verify_total
from .errors import NegativeAllocationError, RefundError
from .models import (
    Coupon,
    Order,
    OrderItem,
    RefundLine,
    RefundResult,
    SettledLine,
    Settlement,
)
from .validators import validate_order, validate_refund_amounts, validate_return_lines


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
            excluded_categories=[
                str(category)
                for category in coupon_raw.get("excluded_categories", [])
            ],
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

    优惠券和平台补贴共用优惠券定义的类目排除规则。被排除行不参与分摊，
    其分摊额为 0；全部被排除时保留整单未分摊优惠字段。
    """
    validate_order(order)
    gross_total = order.gross_cents

    coupon_discount = order.coupon.discount_cents(gross_total) if order.coupon else 0
    subsidy = order.platform_subsidy_cents
    to_allocate = coupon_discount + subsidy
    excluded_categories = set(order.coupon.excluded_categories) if order.coupon else set()

    basis = [
        (it.line_no, it.gross_cents)
        for it in order.items
        if it.gross_cents > 0 and it.category not in excluded_categories
    ]

    if basis and sum(weight for _, weight in basis) > 0:
        allocated = allocate(to_allocate, basis)
    else:
        allocated = {it.line_no: 0 for it in order.items}

    verify_total(sum(allocated.values()), allocated)
    allocated_total = sum(allocated.values())
    unallocated_discount = to_allocate - allocated_total

    lines = [
        SettledLine(
            line_no=it.line_no,
            sku=it.sku,
            category=it.category,
            gross_cents=it.gross_cents,
            discount_cents=allocated.get(it.line_no, 0),
            paid_cents=it.gross_cents - allocated.get(it.line_no, 0),
        )
        for it in order.items
    ]

    for line in lines:
        if line.paid_cents < 0:
            raise NegativeAllocationError(
                "行实付金额不能为负",
                {
                    "order_id": order.order_id,
                    "line_no": line.line_no,
                    "paid_cents": line.paid_cents,
                },
            )

    return Settlement(
        order_id=order.order_id,
        gross_cents=gross_total,
        coupon_discount_cents=coupon_discount,
        subsidy_cents=subsidy,
        shipping_fee_cents=order.shipping_fee_cents,
        paid_total_cents=(
            sum(ln.paid_cents for ln in lines)
            + order.shipping_fee_cents
            - unallocated_discount
        ),
        lines=lines,
        unallocated_discount_cents=unallocated_discount,
    )


def refund(
    order: Order,
    settlement: Settlement,
    returned_line_nos: Sequence[int] | Iterable[int],
) -> RefundResult:
    """按已结算结果计算部分退货的退款金额。"""
    returned = validate_return_lines(order, returned_line_nos)
    lines = [
        RefundLine(
            line_no=ln,
            sku=order.item(ln).sku,
            refund_cents=settlement.line(ln).paid_cents,
        )
        for ln in returned
    ]

    validate_refund_amounts(order.order_id, lines)

    returned_set = set(returned)
    remaining_paid = sum(
        settlement.line(ln).paid_cents
        for ln in order.line_nos
        if ln not in returned_set
    )
    shipping = order.shipping_fee_cents if remaining_paid == 0 else 0

    if not lines:
        raise RefundError("退货行不能为空", {"order_id": order.order_id})
    return RefundResult(
        order_id=order.order_id,
        returned_line_nos=tuple(returned),
        lines=lines,
        shipping_refund_cents=shipping,
        remaining_paid_cents=remaining_paid,
    )


def preview_refund(
    order: Order,
    returned_line_nos: Sequence[int] | Iterable[int],
) -> RefundResult:
    """预览退款：先完成结算，再计算退款，不产生任何副作用。"""
    settlement = settle(order)
    return refund(order, settlement, returned_line_nos)
```

```file path=src/settlement/service.py
"""服务层：加载订单并输出可序列化结果。"""

from __future__ import annotations

import json
from pathlib import Path

from .engine import build_order, preview_refund, refund, settle


def _load_order(path: str | Path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return build_order(json.load(handle))


def settle_file(path: str | Path) -> dict:
    """加载订单并返回整单结算结果。"""
    return settle(_load_order(path)).to_dict()


def refund_file(path: str | Path, returned_line_nos) -> dict:
    """加载订单并返回退款结果。"""
    order = _load_order(path)
    settlement = settle(order)
    return refund(order, settlement, returned_line_nos).to_dict()


def preview_refund_file(path: str | Path, returned_line_nos) -> dict:
    """加载订单并返回退款预览结果。"""
    return preview_refund(_load_order(path), returned_line_nos).to_dict()
```

```file path=src/settlement/cli.py
"""业务命令行入口。

用法：
    python -m settlement.cli settle --order tests/fixtures/order_standard.json
    python -m settlement.cli refund --order tests/fixtures/order_standard.json --return 1,2
    python -m settlement.cli preview --order tests/fixtures/order_standard.json --return 1,2

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

    p_preview = sub.add_parser("preview", help="预览部分退货的退款金额")
    p_preview.add_argument("--order", required=True, help="订单 JSON 路径")
    p_preview.add_argument("--return", dest="returned", required=True, help="退货行号，逗号分隔")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "settle":
            result = service.settle_file(args.order)
        elif args.command == "preview":
            result = service.preview_refund_file(args.order, _parse_lines(args.returned))
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