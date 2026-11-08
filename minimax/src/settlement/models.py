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
