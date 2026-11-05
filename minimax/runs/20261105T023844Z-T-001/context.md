# 任务 T-001：实现 SAVE30 满减券并修正金额舍入

按照 specs/promotion.md 的 SPEC-PRM-002 与 SPEC-PRM-003，为券码 SAVE30 实现满减：
商品小计大于或等于 200 元时减免 30 元，不足 200 元时抛出 CouponNotApplicableError；
门槛只按商品小计判断，不得把运费计入。优惠券在会员折扣之后应用，两者都不减免运费，
优惠后商品金额不得小于 0。同时按 SPEC-PRC-002 把金额舍入从银行家舍入改为
ROUND_HALF_UP。不得修改 tests/ 下的任何文件；未知券码仍抛 UnknownCouponError。

- 检索词：`实现 SAVE30 满减券并修正金额舍入 按照 specs promotion SPEC PRM 为券码 实现满减 商品小计大于或等于 元时减免 不足 元时抛出 CouponNotApplicableError 门槛只按商品小计判断 不得把运费计入 优惠券在会员折扣之后应用 两者都不减免运费 优惠后商品金额不得小于 同时按 PRC 把金额舍入从银行家舍入改为 ROUND_HALF_UP 不得修改 tests 下的任何文件 未知券码仍抛 UnknownCouponError test_coupon 条目标测试由失败变为全部通过 test_money test_pricing test_service 条回归测试保持通过 修改只涉及 src 下的生产代码 目录逐字节不变 apply_coupon quote round_money service money test applies at threshold`
- 预算：3600 tokens，实际 835 tokens
- 候选：46 条（来自 seekdb 全文检索 + 精确来源匹配）

## 一、适用规格

### SPEC-PRM-002

来源：`specs/promotion.md:12-15`

```
## SPEC-PRM-002 SAVE30 规则

券码 `SAVE30`：当商品小计大于或等于 200 元时减免 30 元；商品小计不足 200 元时抛出
`CouponNotApplicableError`。门槛判断只使用商品小计（SPEC-PRC-003），不得把运费计入。
```

### SPEC-PRM-003

来源：`specs/promotion.md:17-20`

```
## SPEC-PRM-003 叠加顺序

先计算会员折扣（SPEC-PRC-005），再应用优惠券；两者都只作用于商品小计。
优惠后的商品金额不得小于 0；若优惠超过商品金额，按 0 计。
```

### SPEC-PRM-004

来源：`specs/promotion.md:22-24`

```
## SPEC-PRM-004 优惠不含运费

优惠券与会员折扣均不减免运费。运费始终按 SPEC-PRC-004 单独计算，优惠金额不得冲抵运费。
```

### SPEC-PRC-002

来源：`specs/pricing.md:15-18`

```
## SPEC-PRC-002 舍入规则

所有金额结果按 `ROUND_HALF_UP`（四舍五入）保留到分，禁止使用 Python `Decimal` 的默认银行家舍入
（`ROUND_HALF_EVEN`）。例如 `0.125` 元必须舍入为 `0.13` 元，`1.005` 元必须舍入为 `1.01` 元。
```

## 二、需要修改的代码

### order_service.service.quote

来源：`src/order_service/service.py:22-44`

```
def quote(items: object, coupon_code: str | None = None) -> Quote:
    """Price ``items`` with the optional coupon and the member discount."""
    lines = tuple(items)
    if not lines:
        raise ValueError("order requires at least one item")

    goods = subtotal(lines)
    member = member_discount(goods)
    coupon = money("0")
    if coupon_code is not None:
        coupon = apply_coupon(goods, coupon_code)

    discounted_goods = goods - member - coupon
    if discounted_goods < 0:
        discounted_goods = money("0")
    shipping = shipping_fee(goods)
    return Quote(
        subtotal=goods,
        member_discount=member,
        coupon_discount=coupon,
        shipping=shipping,
        total=round_money(discounted_goods + shipping),
    )
```

### order_service.promotion.apply_coupon

来源：`src/order_service/promotion.py:27-30`

```
def apply_coupon(amount: Decimal, code: str) -> Decimal:
    """Return the discount for ``code`` on the goods amount."""
    rule = resolve_coupon(code)
    return rule.apply(amount)
```

### order_service.money.round_money

来源：`src/order_service/money.py:13-15`

```
def round_money(value: object) -> Decimal:
    """Round an amount to cents using the platform default rounding mode."""
    return money(value).quantize(CENT, rounding=ROUND_HALF_EVEN)
```

## 三、验证用测试

### tests/test_coupon.py::test_save30_threshold_excludes_shipping

来源：`tests/test_coupon.py:29-32`

```
def test_save30_threshold_excludes_shipping() -> None:
    # 195 + 12 shipping = 207; the threshold must still judge on 195 goods only.
    with pytest.raises(CouponNotApplicableError):
        quote([item("195.00")], coupon_code="SAVE30")
```

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

### tests/test_coupon.py::test_save30_applies_at_threshold

来源：`tests/test_coupon.py:18-21`

```
def test_save30_applies_at_threshold() -> None:
    priced = quote([item("200.00")], coupon_code="SAVE30")
    assert priced.coupon_discount == Decimal("30.00")
    assert priced.total == Decimal("162.00")
```

### tests/test_coupon.py::test_save30_stacks_after_member_discount

来源：`tests/test_coupon.py:35-39`

```
def test_save30_stacks_after_member_discount() -> None:
    priced = quote([item("250.00")], coupon_code="SAVE30")
    assert priced.member_discount == Decimal("25.00")
    assert priced.coupon_discount == Decimal("30.00")
    assert priced.total == Decimal("207.00")
```

### tests/test_coupon.py::test_money_rounding_is_half_up

来源：`tests/test_coupon.py:47-49`

```
def test_money_rounding_is_half_up() -> None:
    assert round_money("0.125") == Decimal("0.13")
    assert round_money("1.005") == Decimal("1.01")
```

## 四、历史任务状态（PowerContext）

- 无历史记录（本次从空状态开始）
