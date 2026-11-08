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


def validate_refund_amounts(
    order_id: str, lines: Sequence[RefundLine]
) -> None:
    """退款明细出现负金额时显式失败。"""
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
