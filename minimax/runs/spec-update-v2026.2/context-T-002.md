# 任务 T-002：SAVE30 门槛调整为 150 元（规格 v2026.2）

规格升级到 v2026.2：SAVE30 的门槛由 200 元调整为 150 元，其余规则不变。
本任务不修改代码，只要求上下文模块在重新导入规格后，返回更新后的 SPEC-PRM-002 文本，
并带出 T-001 留下的验证结果与待办状态。

- 检索词：`SAVE30 门槛调整为 规格 v2026 规格升级到 的门槛由 元调整为 其余规则不变 本任务不修改代码 只要求上下文模块在重新导入规格后 返回更新后的 SPEC PRM 文本 并带出 留下的验证结果与待办状态 上下文中的 文本包含 且不含 元门槛 上下文包含 的运行状态与验证结果 apply_coupon promotion`
- 预算：3600 tokens，实际 546 tokens
- 候选：27 条（来自 seekdb 全文检索 + 精确来源匹配）

## 一、适用规格

### SPEC-PRM-002

来源：`specs/promotion.md:12-15`

```
## SPEC-PRM-002 SAVE30 规则

券码 `SAVE30`：当商品小计大于或等于 150 元时减免 30 元；商品小计不足 150 元时抛出
`CouponNotApplicableError`。门槛判断只使用商品小计（SPEC-PRC-003），不得把运费计入。
```

### SPEC-PRC-004

来源：`specs/pricing.md:24-27`

```
## SPEC-PRC-004 运费

商品小计大于或等于 300 元时免运费；否则运费为 12 元。
免运费门槛只按商品小计判断，不使用会员折扣或优惠券抵扣后的金额。
```

### SPEC-PRC-002

来源：`specs/pricing.md:15-18`

```
## SPEC-PRC-002 舍入规则

所有金额结果按 `ROUND_HALF_UP`（四舍五入）保留到分，禁止使用 Python `Decimal` 的默认银行家舍入
（`ROUND_HALF_EVEN`）。例如 `0.125` 元必须舍入为 `0.13` 元，`1.005` 元必须舍入为 `1.01` 元。
```

### SPEC-SHP-001

来源：`specs/shipping.md:8-10`

```
## SPEC-SHP-001 免运费与优惠叠加

免运费门槛按商品小计判断。优惠券与会员折扣不改变免运费判断，也不抵扣已经产生的运费。
```

## 二、需要修改的代码

### order_service.promotion.apply_coupon

来源：`src/order_service/promotion.py:27-30`

```
def apply_coupon(amount: Decimal, code: str) -> Decimal:
    """Return the discount for ``code`` on the goods amount."""
    rule = resolve_coupon(code)
    return rule.apply(amount)
```

### order_service.promotion::<module>

来源：`src/order_service/promotion.py:1-7`

```
"""Coupon registry and coupon application."""

from decimal import Decimal

from .money import money
```

### order_service.promotion.UnknownCouponError

来源：`src/order_service/promotion.py:8-9`

```
class UnknownCouponError(ValueError):
    """Raised when a coupon code is not registered."""
```

### order_service.promotion.CouponNotApplicableError

来源：`src/order_service/promotion.py:12-13`

```
class CouponNotApplicableError(ValueError):
    """Raised when a known coupon does not apply to this order."""
```

### order_service.promotion.resolve_coupon

来源：`src/order_service/promotion.py:19-24`

```
def resolve_coupon(code: str) -> object:
    """Look up a coupon rule by code."""
    try:
        return COUPONS[code]
    except KeyError as exc:
        raise UnknownCouponError(f"unknown coupon: {code}") from exc
```

### order_service.promotion.no_discount

来源：`src/order_service/promotion.py:33-35`

```
def no_discount() -> Decimal:
    """A convenience zero used by callers that do not apply a coupon."""
    return money("0")
```

## 三、验证用测试

### tests/test_coupon.py::test_save30_is_rejected_below_threshold

来源：`tests/test_coupon.py:24-26`

```
def test_save30_is_rejected_below_threshold() -> None:
    with pytest.raises(CouponNotApplicableError):
        quote([item("199.99")], coupon_code="SAVE30")
```

### tests/test_coupon.py::test_save30_does_not_reduce_shipping

来源：`tests/test_coupon.py:42-44`

```
def test_save30_does_not_reduce_shipping() -> None:
    priced = quote([item("250.00")], coupon_code="SAVE30")
    assert priced.shipping == Decimal("12.00")
```

## 四、历史任务状态（PowerContext）

- [已完成] `20261105T023844Z-T-001` passed（2026-11-05T02:38:45Z）：passed：改动 src/order_service/money.py, src/order_service/promotion.py；目标测试 6 条，回归 13 条通过
- [已有验证结果] `20261105T023844Z-T-001` passed（2026-11-05T02:38:45Z）：目标测试 6/6 通过；回归 13 条通过
- [待办] `20261105T023844Z-T-001` closed（2026-11-05T02:38:45Z）：T-002 规格变更复核（门槛 200→150）

