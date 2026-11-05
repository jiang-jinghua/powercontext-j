# 验证样例

## 1. 业务工程

`demo/order-service/`：订单计价服务，纯 Python，`pytest` 可运行。初始状态 19 条测试中
13 条通过、6 条失败（目标功能尚未实现）。

## 2. 任务（T-001）

> 按照 `specs/promotion.md` 的 SPEC-PRM-002 与 SPEC-PRM-003 实现券码 SAVE30：商品小计满
> 200 元减 30 元，门槛不含运费；优惠券在会员折扣之后应用，运费不参与优惠。同时按
> SPEC-PRC-002 把金额舍入改为 ROUND_HALF_UP。不得修改 `tests/`。

**要改什么**：`promotion.py` 注册 SAVE30 规则并在不足门槛时抛 `CouponNotApplicableError`；
`money.py` 改用 `ROUND_HALF_UP`。

**哪些原有行为必须保持**：

| 行为 | 回归测试 |
| --- | --- |
| 商品小计按行金额求和 | `tests/test_pricing.py::test_subtotal_sums_line_amounts` |
| 满 300 免运费、否则 12 元 | `test_shipping_is_free_at_threshold` / `test_shipping_is_charged_below_threshold` |
| 满 100 会员折扣 10%、否则 0 | `test_member_discount_applies_at_threshold` / `test_member_discount_is_zero_below_threshold` |
| 无券订单总价 | `test_quote_without_coupon_applies_member_discount` |
| 未知券码抛 `UnknownCouponError` | `test_unknown_coupon_is_rejected` |
| 空购物车拒绝 | `test_empty_cart_is_rejected` |

## 3. 有效规格（节选）

- `SPEC-PRC-002`：金额一律 `ROUND_HALF_UP`；`0.125 → 0.13`，`1.005 → 1.01`。
- `SPEC-PRM-002`：SAVE30 商品小计 ≥ 200 减 30；不足抛 `CouponNotApplicableError`；门槛不含运费。
- `SPEC-PRM-003`：先会员折扣、再优惠券；优惠后商品金额不得小于 0。
- `SPEC-PRM-004`：优惠不减免运费。
- `SPEC-PRC-004`：商品小计 ≥ 300 免运费，否则 12 元。

## 4. 初始数据

| 文件 | 作用 |
| --- | --- |
| `specs/pricing.md`、`promotion.md`、`shipping.md`、`refund.md` | 4 份规格，12 个条款（refund 为无关干扰项） |
| `src/order_service/money.py` | 当前使用 `ROUND_HALF_EVEN`（不满足 SPEC-PRC-002） |
| `src/order_service/promotion.py` | `COUPONS` 为空，SAVE30 未注册 |
| `src/order_service/service.py` | 计价顺序已就绪，未知券码会抛错 |
| `tests/test_coupon.py` | 6 条目标测试（当前失败） |
| `tests/test_money.py`、`test_pricing.py`、`test_service.py` | 13 条回归测试（当前通过） |

## 5. 操作步骤

```bash
export PYTHONPATH="$PWD/src"
python -m sddctx reset
python -m sddctx init
python -m sddctx import
python -m sddctx context T-001 --json      # 检查来源
python -m sddctx run T-001 --provider minimax
python -m sddctx status T-001
```

## 6. 预期结果

| 检查 | 预期 |
| --- | --- |
| 上下文规格 | 含 `SPEC-PRM-002`、`SPEC-PRM-003`、`SPEC-PRM-004`、`SPEC-PRC-002`，来源为 `specs/*.md:行号` |
| 上下文代码 | 含 `apply_coupon`、`quote`、`round_money` |
| 上下文测试 | 含 `tests/test_coupon.py` 的 6 条目标测试 |
| 修改前 | 6 条目标测试失败（`UnknownCouponError` 或舍入断言失败），13 条回归通过 |
| 修改后 | 19 条全部通过，`no_new_failures=true`、`tests_untouched=true` |
| 补丁范围 | 仅 `src/order_service/money.py`、`promotion.py` |
| 状态 | `status T-001` 显示 run/validation/todo 三条记录 |

## 7. 规格变更样例（T-002）

```bash
python -m sddctx import --spec-version 2026.2 \
  --spec-overlay demo/spec-updates/2026.2/promotion.md
python -m sddctx context T-002 --json
```

预期：`SPEC-PRM-002` 文本为「大于或等于 150 元」；`state` 含 T-001 的三条记录。

## 8. 重置

```bash
python -m sddctx reset
rm -rf runs
```

`demo/order-service/` 不会被修改，重置后可直接重跑。
