# 技术使用记录

> 由 `scripts/make_record.py` 从 `20261105T085752Z-T-001/` 生成；重新执行 `python -m sddctx run ...` 后会得到新的运行目录。

## 0. 记录范围

- 运行 ID：`20261105T085752Z-T-001`
- 运行状态：`passed`
- 模型提供方：`minimax` / `MiniMax-M3.1-Flash`
- 说明：本次提交的 `runs/` 样例运行使用 **MiniMax** 实际生成补丁。

## 1. MiniMax 使用记录

| 项 | 值 |
| --- | --- |
| 端点 | `POST /v1/messages` |
| 模型 | `MiniMax-M3.1-Flash` |
| 请求 ID | `073b78117391b88b6891ef8cf496c7bf` |
| 用量 | `{"input_tokens": 175, "output_tokens": 915, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 2304, "output_tokens_details": {"thinking_tokens": 166}}` |
| 提示词 SHA-256 | `f06d0ae2845a9e6c762d0176bdee0f5e82d3ebb9b9713021a26c6b822237a4a9` |
| 补丁 SHA-256 | `81ed5e0774ecd7f02cf8d5018c08afae3dbef53fceff61fcd98db4f45311496f` |

## 2. 上下文（检索结果）

- 查询词：`实现 SAVE30 满减券并修正金额舍入 按照 specs promotion SPEC PRM 为券码 实现满减 商品小计大于或等于 元时减免 不足 元时抛出 CouponNotApplicableError 门槛只按商品小计判断 不得把运费计入 优惠券在会员折扣之后应用 两者都不减免运费 优惠后商品金额不得小于 同时按 PRC 把金额舍入从银行家舍入改为 ROUND_HALF_UP 不得修改 tests 下的任何文件 未知券码仍抛 UnknownCouponError 关键约束 的门槛判断与 元抵扣都以 商品小计 为准 service quote 中必须用 商品小计调用 apply_coupon 不能传入会员折扣后的金额 goods member 是错误写法 test_coupon 条目标测试由失败变为全部通过 test_money test_pricing test_service`
- 候选行数：40；选中 13 条；
  835 / 3600 tokens；截断：False

| 类别 | 来源 |
| --- | --- |
| 规格 `SPEC-PRM-002` | `specs/promotion.md:12-15` |
| 规格 `SPEC-PRM-003` | `specs/promotion.md:17-20` |
| 规格 `SPEC-PRM-004` | `specs/promotion.md:22-24` |
| 规格 `SPEC-PRC-002` | `specs/pricing.md:15-18` |
| 代码 `order_service.service.quote` | `src/order_service/service.py:22-44` |
| 代码 `order_service.promotion.apply_coupon` | `src/order_service/promotion.py:27-30` |
| 代码 `order_service.money.round_money` | `src/order_service/money.py:13-15` |
| 测试 `tests/test_coupon.py::test_save30_is_rejected_below_threshold` | `tests/test_coupon.py:24-26` |
| 测试 `tests/test_coupon.py::test_save30_threshold_excludes_shipping` | `tests/test_coupon.py:29-32` |
| 测试 `tests/test_coupon.py::test_save30_does_not_reduce_shipping` | `tests/test_coupon.py:42-44` |
| 测试 `tests/test_coupon.py::test_save30_applies_at_threshold` | `tests/test_coupon.py:18-21` |
| 测试 `tests/test_coupon.py::test_save30_stacks_after_member_discount` | `tests/test_coupon.py:35-39` |
| 测试 `tests/test_coupon.py::test_money_rounding_is_half_up` | `tests/test_coupon.py:47-49` |

## 3. seekdb / OceanBase 使用记录

- 存储：嵌入式 seekdb（`pylibseekdb`），版本 `5.7.25-OceanBase seekdb-v1.4.0.0`
- 语料：`sdd_document` 13 行，`sdd_chunk` {'code': 18, 'spec': 12, 'test': 26}
- 最近导入：`{"import_id": "imp_1793869092", "project": "order-service", "spec_version": "2026.2", "documents": 13, "chunks": 56, "imported_at": "2026-11-05T08:58:12Z"}`
- 全文检索：`MATCH(text) AGAINST(...)` 命中 34 行，耗时 57.203 ms（`db-queries.json` 第 5 条）

关键 SQL（截断自 `db-queries.json`）：

```sql
SELECT chunk_id, doc_id, kind, symbol, path, start_line, end_line, tokens, text, MATCH(text) AGAINST ('实现 SAVE30 满减券并修正金额舍入 按照 specs promotion SPEC PRM 为券码 实现满减 商品小计大于或等于 元时减免 不足 元时抛出 CouponNotApplicableError 门槛只按商品小计判断 不得把运费计入 优惠券在会员折扣之后应用 两者都不减免运费 优惠后商品金额不得小于 同时按 PRC 把金额舍入从银行家舍入改为 ROUND_HALF_UP 不得修改 tests 下的任何文件 未知券码仍抛 UnknownCouponError 关键约束 的门槛判断与 元抵扣都以 商品小计 为准 service quote 中必须用 商品小计调用 apply_
```

精确来源查询（规格编号/符号/路径）：

```sql
SELECT chunk_id, doc_id, kind, symbol, path, start_line, end_line, tokens, text, 0 AS score
FROM sdd_chunk WHERE project = 'order-service' AND (symbol IN ('SPEC-PRM-002', ...) OR path IN (...))
```

## 4. PowerContext 使用记录

运行时：`powercontext[server]==1.2.0`，进程内 `open_builtin_runtime`，默认 Scope 的 Memory 工件，
数据库 `.sdd/state.db`（SQLite）。每次运行追加 `run` / `validation` / `todo` 三类条目。

`python -m sddctx status T-001` 在独立进程中读回的内容：

```text
# PowerContext 任务状态

- `2026-11-05T08:57:58Z` [run] T-001 passed — passed：改动 src/order_service/money.py, src/order_service/promotion.py；目标测试 6 条，回归 13 条通过
- `2026-11-05T08:57:58Z` [validation] T-001 passed — 目标测试 6/6 通过；回归 13 条通过
  - no_new_failures: True
  - ok: True
  - regression_passed: ['tests/test_money.py::test_money_parses_exact_decimal', 'tests/test_money.py::test_round_money_handles_whole_numbers', 'tests/test_money.py::test_round_money_keeps_cents', 'tests/test_pricing.py::test_member_discount_applies_at_threshold', 'tests/test_pricing.py::test_member_discount_is_zero_below_threshold', 'tests/test_pricing.py::test_member_discount_rounds_to_cents', 'tests/test_pricing.py::test_shipping_is_charged_below_threshold', 'tests/test_pricing.py::test_shipping_is_free_at_threshold', 'tests/test_pricing.py::test_subtotal_sums_line_amounts', 'tests/test_service.py::test_empty_cart_is_rejected', 'tests/test_service.py::test_quote_is_free_shipping_above_threshold', 'tests/test_service.py::test_quote_without_coupon_applies_member_discount', 'tests/test_service.py::test_unknown_coupon_is_rejected']
  - target_failed_before: ['tests/test_coupon.py::test_money_rounding_is_half_up', 'tests/test_coupon.py::test_save30_applies_at_threshold', 'tests/test_coupon.py::test_save30_does_not_reduce_shipping', 'tests/test_coupon.py::test_save30_is_rejected_below_threshold', 'tests/test_coupon.py::test_save30_stacks_after_member_discount', 'tests/test_coupon.py::test_save30_threshold_excludes_shipping']
  - target_passed_after: ['tests/test_coupon.py::test_money_rounding_is_half_up', 'tests/test_coupon.py::test_save30_applies_at_threshold', 'tests/test_coupon.py::test_save30_does_not_reduce_shipping', 'tests/test_coupon.py::test_save30_is_rejected_below_threshold', 'tests/test_coupon.py::test_save30_stacks_after_member_discount', 'tests/test_coupon.py::test_save30_threshold_excludes_shipping']
  - target_tests: ['tests/test_coupon.py::test_money_rounding_is_half_up', 'tests/test_coupo
```

## 5. 代码差异与测试

| 文件 | 新增 | 删除 |
| --- | ---: | ---: |
| `src/order_service/money.py` | 3 | 3 |
| `src/order_service/promotion.py` | 18 | 1 |

- 目标测试：6 条；修改前失败 6 条；修改后通过 6 条
- 回归通过：13 条；无新增失败：True
- 测试目录 SHA-256：修改前 `9b8ccfa17db579af…`，修改后 `9b8ccfa17db579af…`；未改动：True
- 测试日志：`20261105T085752Z-T-001/pytest-before.log`、`20261105T085752Z-T-001/pytest-after.log`

## 6. 规格变更（v2026.2）

- 重新导入：`{"import_id": "imp_1793869092", "project": "order-service", "spec_version": "2026.2", "documents": 13, "chunks": 56, "imported_at": "2026-11-05T08:58:12Z"}`
- T-002 上下文中的 `SPEC-PRM-002`：

```text
## SPEC-PRM-002 SAVE30 规则

券码 `SAVE30`：当商品小计大于或等于 150 元时减免 30 元；商品小计不足 150 元时抛出
`CouponNotApplicableError`。门槛判断只使用商品小计（SPEC-PRC-003），不得把运费计入。
```

- T-002 读取到的状态：

```text
[run] 20261105T085752Z-T-001 passed — passed：改动 src/order_service/money.py, src/order_service/promotion.py；目标测试 6 条，回归 13 条通过
[validation] 20261105T085752Z-T-001 passed — 目标测试 6/6 通过；回归 13 条通过
[todo] 20261105T085752Z-T-001 closed — T-002 规格变更复核（门槛 200→150）
```

## 7. 交付说明

- `runs/` 中的样例是离线复核记录；评审用 `--provider minimax` 重跑会生成真实的 MiniMax 请求/响应。
- `docs/technical-record.md` 可在每次运行后由 `scripts/make_record.py` 重新生成。
