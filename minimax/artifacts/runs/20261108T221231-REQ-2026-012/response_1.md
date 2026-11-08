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
    excluded_categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "excluded_categories",
            tuple(str(category) for category in self.excluded_categories),
        )

    def discount_cents(self, gross_cents: int) -> int:
        """按券面规则计算优惠金额（不含分摊）。"""
        if gross_cents < self.min_spend_cents:
            return 0
        if self.type == "amount":
            return min(self.value, gross_cents)
        if self.type == "percent":
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
            "paid_total_cents": self.paid_total_cents,
            "unallocated_discount_cents": self.unallocated_discount_cents,
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
            excluded_categories=tuple(
                str(category)
                for category in coupon_raw.get("excluded_categories", [])
            ),
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

    待分摊总额 = 优惠券金额 + 平台补贴。优惠券排除的类目不参与分摊。
    """
    validate_order(order)
    gross_total = order.gross_cents

    coupon_discount = order.coupon.discount_cents(gross_total) if order.coupon else 0
    subsidy = order.platform_subsidy_cents
    to_allocate = coupon_discount + subsidy
    excluded_categories = set(
        order.coupon.excluded_categories if order.coupon else ()
    )

    basis = [
        (it.line_no, it.gross_cents)
        for it in order.items
        if it.gross_cents > 0 and it.category not in excluded_categories
    ]
    allocated = {it.line_no: 0 for it in order.items}
    unallocated_discount = 0
    if basis:
        allocations = allocate(to_allocate, basis)
        allocated.update(allocations)
        verify_total(to_allocate, allocations)
    else:
        unallocated_discount = to_allocate

    lines = []
    for it in order.items:
        discount = allocated[it.line_no]
        paid = it.gross_cents - discount
        if paid < 0:
            raise NegativeAllocationError(
                "分摊后行实付金额不能为负",
                {
                    "order_id": order.order_id,
                    "line_no": it.line_no,
                    "paid_cents": paid,
                },
            )
        lines.append(
            SettledLine(
                line_no=it.line_no,
                sku=it.sku,
                category=it.category,
                gross_cents=it.gross_cents,
                discount_cents=discount,
                paid_cents=paid,
            )
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
    if not lines:
        raise RefundError("退货行不能为空", {"order_id": order.order_id})

    validate_refund_amounts(order.order_id, lines)
    returned_set = set(returned)
    remaining_paid = sum(
        ln.paid_cents
        for ln in settlement.lines
        if ln.line_no not in returned_set
    )
    shipping = order.shipping_fee_cents if remaining_paid == 0 else 0
    return RefundResult(
        order_id=order.order_id,
        returned_line_nos=tuple(returned),
        lines=lines,
        shipping_refund_cents=shipping,
        remaining_paid_cents=remaining_paid,
    )


def preview_refund(
    order: Order, returned_line_nos: Sequence[int] | Iterable[int]
) -> RefundResult:
    """只读预览退款结果：内部完成结算与退款计算。"""
    return refund(order, settle(order), returned_line_nos)
```