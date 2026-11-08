"""结算领域异常定义。

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
