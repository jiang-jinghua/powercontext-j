## system

你是资深 Python 工程师，只输出一个可被 `git apply -p1` 直接应用的 unified diff。
硬性要求：
1. 只修改生产代码，绝不修改 tests/ 下的任何文件，也不要新增测试。
2. 严格遵守给出的规格条目；不确定时保持现有公开 API 不变。
3. diff 必须包含 `--- a/<path>`、`+++ b/<path>` 与 `@@` hunk 头，路径相对工程根目录。
4. 不要输出解释、Markdown 代码围栏以外的内容；不要输出 diff 以外的文本。
5. 所有金额使用 decimal.Decimal，禁止 float；金额舍入遵循规格。

## user

## 任务
T-001：实现 SAVE30 满减券并修正金额舍入

按照 specs/promotion.md 的 SPEC-PRM-002 与 SPEC-PRM-003，为券码 SAVE30 实现满减：
商品小计大于或等于 200 元时减免 30 元，不足 200 元时抛出 CouponNotApplicableError；
门槛只按商品小计判断，不得把运费计入。优惠券在会员折扣之后应用，两者都不减免运费，
优惠后商品金额不得小于 0。同时按 SPEC-PRC-002 把金额舍入从银行家舍入改为
ROUND_HALF_UP。不得修改 tests/ 下的任何文件；未知券码仍抛 UnknownCouponError。

验收条件：
- tests/test_coupon.py 的 6 条目标测试由失败变为全部通过
- tests/test_money.py、tests/test_pricing.py、tests/test_service.py 的 13 条回归测试保持通过
- 修改只涉及 src/ 下的生产代码，tests/ 目录逐字节不变

## 适用规格（必须逐条满足）
[SPEC-PRM-002] specs/promotion.md:12-15
## SPEC-PRM-002 SAVE30 规则

券码 `SAVE30`：当商品小计大于或等于 200 元时减免 30 元；商品小计不足 200 元时抛出
`CouponNotApplicableError`。门槛判断只使用商品小计（SPEC-PRC-003），不得把运费计入。

[SPEC-PRM-003] specs/promotion.md:17-20
## SPEC-PRM-003 叠加顺序

先计算会员折扣（SPEC-PRC-005），再应用优惠券；两者都只作用于商品小计。
优惠后的商品金额不得小于 0；若优惠超过商品金额，按 0 计。

[SPEC-PRM-004] specs/promotion.md:22-24
## SPEC-PRM-004 优惠不含运费

优惠券与会员折扣均不减免运费。运费始终按 SPEC-PRC-004 单独计算，优惠金额不得冲抵运费。

[SPEC-PRC-002] specs/pricing.md:15-18
## SPEC-PRC-002 舍入规则

所有金额结果按 `ROUND_HALF_UP`（四舍五入）保留到分，禁止使用 Python `Decimal` 的默认银行家舍入
（`ROUND_HALF_EVEN`）。例如 `0.125` 元必须舍入为 `0.13` 元，`1.005` 元必须舍入为 `1.01` 元。

## 现有代码
FILE src/order_service/service.py (22-44)
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

FILE src/order_service/promotion.py (27-30)
def apply_coupon(amount: Decimal, code: str) -> Decimal:
    """Return the discount for ``code`` on the goods amount."""
    rule = resolve_coupon(code)
    return rule.apply(amount)

FILE src/order_service/money.py (13-15)
def round_money(value: object) -> Decimal:
    """Round an amount to cents using the platform default rounding mode."""
    return money(value).quantize(CENT, rounding=ROUND_HALF_EVEN)

## 验证测试（不得修改）
[tests/test_coupon.py::test_save30_threshold_excludes_shipping] tests/test_coupon.py:29-32
def test_save30_threshold_excludes_shipping() -> None:
    # 195 + 12 shipping = 207; the threshold must still judge on 195 goods only.
    with pytest.raises(CouponNotApplicableError):
        quote([item("195.00")], coupon_code="SAVE30")

[tests/test_coupon.py::test_save30_is_rejected_below_threshold] tests/test_coupon.py:24-26
def test_save30_is_rejected_below_threshold() -> None:
    with pytest.raises(CouponNotApplicableError):
        quote([item("199.99")], coupon_code="SAVE30")

[tests/test_coupon.py::test_save30_does_not_reduce_shipping] tests/test_coupon.py:42-44
def test_save30_does_not_reduce_shipping() -> None:
    priced = quote([item("250.00")], coupon_code="SAVE30")
    assert priced.shipping == Decimal("12.00")

[tests/test_coupon.py::test_save30_applies_at_threshold] tests/test_coupon.py:18-21
def test_save30_applies_at_threshold() -> None:
    priced = quote([item("200.00")], coupon_code="SAVE30")
    assert priced.coupon_discount == Decimal("30.00")
    assert priced.total == Decimal("162.00")

[tests/test_coupon.py::test_save30_stacks_after_member_discount] tests/test_coupon.py:35-39
def test_save30_stacks_after_member_discount() -> None:
    priced = quote([item("250.00")], coupon_code="SAVE30")
    assert priced.member_discount == Decimal("25.00")
    assert priced.coupon_discount == Decimal("30.00")
    assert priced.total == Decimal("207.00")

[tests/test_coupon.py::test_money_rounding_is_half_up] tests/test_coupon.py:47-49
def test_money_rounding_is_half_up() -> None:
    assert round_money("0.125") == Decimal("0.13")
    assert round_money("1.005") == Decimal("1.01")

## 历史任务状态（来自 PowerContext）
（无历史记录）

## 输出
只输出 unified diff，路径相对工程根目录。修改后用 `python -m pytest -q` 应全部通过。