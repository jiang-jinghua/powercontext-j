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
