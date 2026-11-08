"""面向调用方的服务层。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Sequence

from .engine import build_order, preview_refund, refund, settle
from .models import Order, RefundResult, Settlement


def load_order(path: str | Path) -> Order:
    """从 JSON 文件加载订单。"""
    with Path(path).open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return build_order(payload)


def settle_file(path: str | Path) -> dict:
    """加载并结算订单，返回可序列化结果。"""
    return settle(load_order(path)).to_dict()


def refund_file(
    path: str | Path, returned_line_nos: Sequence[int] | Iterable[int]
) -> dict:
    """加载订单，计算退款并返回可序列化结果。"""
    order = load_order(path)
    result = refund(order, settle(order), returned_line_nos)
    return result.to_dict()


def preview_refund_file(
    path: str | Path, returned_line_nos: Sequence[int] | Iterable[int]
) -> dict:
    """只读预览退款结果，返回可序列化结果。"""
    return preview_refund(load_order(path), returned_line_nos).to_dict()
