# 错误码表

错误码稳定、可断言，新增错误码需同步更新本文件。

| 错误码 | 异常类 | 触发条件 | 处理建议 |
| --- | --- | --- | --- |
| `E_SETTLEMENT` | `SettlementError` | 结算领域未分类错误（基类默认值） | 视为缺陷排查 |
| `E_BAD_INPUT` | `ValidationError` | 订单输入非法：行号重复/非正、数量非正、单价为负、补贴或运费为负、优惠券类型未知 | 修正入参后重试 |
| `E_ALLOCATION` | `AllocationError` | 分摊基数非法：为空、全 0、含负值、待分摊金额为负、未知尾差策略 | 检查订单行金额 |
| `E_REFUND` | `RefundError` | 退货行号为空中或不在订单内 | 校验退货请求 |
| `E_NEG_ALLOC` | `NegativeAllocationError` | 分摊后某行实付为负（SPEC-011-04） | 核对优惠券与类目排除配置，禁止用夹断掩盖 |
| `E_NEG_REFUND` | `NegativeRefundError` | 退款明细金额为负（SPEC-011-04） | 同上，需人工介入 |

## 约定

1. 异常统一继承 `SettlementError`，都带 `code`、`message`、`detail`，`detail` 至少包含定位所需的主键
   （`order_id` / `line_no` / 金额）。
2. CLI 捕获 `SettlementError` 后输出 `{"error_code": ..., "message": ..., "detail": ...}` 并以退出码 `2` 结束。
3. **禁止**用 `max(0, x)` 之类的静默夹断处理负金额：负值代表规则冲突，必须显式失败并留下证据。
