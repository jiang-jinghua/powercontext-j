# order-service（验证样例工程）

一个最小但可运行、带测试的订单计价服务，用于演示 SDD 上下文模块如何把
规格（specs/）、代码（src/）和测试（tests/）组织成一次真实代码修改所需的上下文。

## 运行

```bash
python -m pytest -q
```

初始状态下 `tests/test_coupon.py` 中与 SAVE30、HALF_UP 相关的用例失败，
其余用例通过；完成 `tasks/T-001.yaml` 描述的修改后全部通过。

## 结构

| 路径 | 内容 |
| --- | --- |
| `specs/pricing.md` | 金额、舍入、小计、运费、会员折扣 |
| `specs/promotion.md` | 券码解析、SAVE30、叠加顺序 |
| `specs/shipping.md` | 免运费与优惠叠加 |
| `specs/refund.md` | 退款（与本任务无关的干扰项） |
| `src/order_service/` | 计价实现 |
| `tests/` | 回归测试 + 目标功能测试 |
