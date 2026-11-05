# 技术使用记录

> 由 `scripts/make_record.py` 从 `20261105T023844Z-T-001/` 生成；重新执行 `python -m sddctx run ...` 后会得到新的运行目录。

## 0. 记录范围

- 运行 ID：`20261105T023844Z-T-001`
- 运行状态：`passed`
- 模型提供方：`manual` / `manual-patch`
- 说明：本次提交的 `runs/` 样例运行使用 **manual** 提供方（审阅过的补丁），**没有调用 MiniMax**；MiniMax 调用链由单元测试和本地回环 stub 验证。

## 1. MiniMax 使用记录

本机交付时没有可用的 MiniMax API Key，因此 `runs/` 中的样例用 `--provider manual` 完成，
产物中 `model/manual-source.json` 明确记录 `provider=manual`，不冒充模型调用。
真实运行方式：

```bash
export MINIMAX_API_KEY=...
export MINIMAX_BASE_URL=https://api.minimaxi.com   # 国际站用 https://api.minimax.io
python -m sddctx reset && python -m sddctx init && python -m sddctx import
python -m sddctx run T-001 --provider minimax
python scripts/make_record.py
```

### 1.1 已完成的调用链验证

- 单元测试 `tests/test_minimax.py`：请求体、`base_resp` 业务错误、5xx 重试、空回复拒绝、manual 标记。
- 端到端 stub：`scripts/minimax_stub.py` 在本地回环模拟 `/v1/text/chatcompletion_v2`，
  `MINIMAX_BASE_URL=http://127.0.0.1:8787 MINIMAX_API_KEY=stub python -m sddctx run T-001 --provider minimax`
  能完成「HTTP 请求 → 响应解析 → diff 应用 → 测试通过 → 状态写入」，产物包含 `minimax-request-01.json`、
  `minimax-response-01.json`。该 stub 运行不写入 `runs/`，也不作为 MiniMax 参与证据。

## 2. 上下文（检索结果）

- 查询词：`实现 SAVE30 满减券并修正金额舍入 按照 specs promotion SPEC PRM 为券码 实现满减 商品小计大于或等于 元时减免 不足 元时抛出 CouponNotApplicableError 门槛只按商品小计判断 不得把运费计入 优惠券在会员折扣之后应用 两者都不减免运费 优惠后商品金额不得小于 同时按 PRC 把金额舍入从银行家舍入改为 ROUND_HALF_UP 不得修改 tests 下的任何文件 未知券码仍抛 UnknownCouponError test_coupon 条目标测试由失败变为全部通过 test_money test_pricing test_service 条回归测试保持通过 修改只涉及 src 下的生产代码 目录逐字节不变 apply_coupon quote round_money service money test applies at threshold`
- 候选行数：46；选中 13 条；
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
| 测试 `tests/test_coupon.py::test_save30_threshold_excludes_shipping` | `tests/test_coupon.py:29-32` |
| 测试 `tests/test_coupon.py::test_save30_is_rejected_below_threshold` | `tests/test_coupon.py:24-26` |
| 测试 `tests/test_coupon.py::test_save30_does_not_reduce_shipping` | `tests/test_coupon.py:42-44` |
| 测试 `tests/test_coupon.py::test_save30_applies_at_threshold` | `tests/test_coupon.py:18-21` |
| 测试 `tests/test_coupon.py::test_save30_stacks_after_member_discount` | `tests/test_coupon.py:35-39` |
| 测试 `tests/test_coupon.py::test_money_rounding_is_half_up` | `tests/test_coupon.py:47-49` |

## 3. seekdb / OceanBase 使用记录

- 存储：嵌入式 seekdb（`pylibseekdb`），版本 `5.7.25-OceanBase seekdb-v1.4.0.0`
- 语料：`sdd_document` 13 行，`sdd_chunk` {'code': 18, 'spec': 12, 'test': 26}
- 最近导入：`{"import_id": "imp_1793846329", "project": "order-service", "spec_version": "2026.2", "documents": 13, "chunks": 56, "imported_at": "2026-11-05T02:38:49Z"}`
- 全文检索：`MATCH(text) AGAINST(...)` 命中 46 行，耗时 58.419 ms（`db-queries.json` 第 4 条）

关键 SQL（截断自 `db-queries.json`）：

```sql
SELECT chunk_id, doc_id, kind, symbol, path, start_line, end_line, tokens, text, MATCH(text) AGAINST ('实现 SAVE30 满减券并修正金额舍入 按照 specs promotion SPEC PRM 为券码 实现满减 商品小计大于或等于 元时减免 不足 元时抛出 CouponNotApplicableError 门槛只按商品小计判断 不得把运费计入 优惠券在会员折扣之后应用 两者都不减免运费 优惠后商品金额不得小于 同时按 PRC 把金额舍入从银行家舍入改为 ROUND_HALF_UP 不得修改 tests 下的任何文件 未知券码仍抛 UnknownCouponError test_coupon 条目标测试由失败变为全部通过 test_money test_pricing test_se
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

- `2026-11-05T02:38:45Z` [run] T-001 passed — passed：改动 src/order_service/money.py, src/order_service/promotion.py；目标测试 6 条，回归 13 条通过
- `2026-11-05T02:38:45Z` [validation] T-001 passed — 目标测试 6/6 通过；回归 13 条通过
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
| `src/order_service/promotion.py` | 21 | 2 |

- 目标测试：6 条；修改前失败 6 条；修改后通过 6 条
- 回归通过：13 条；无新增失败：True
- 测试目录 SHA-256：修改前 `9b8ccfa17db579af…`，修改后 `9b8ccfa17db579af…`；未改动：True
- 测试日志：`20261105T023844Z-T-001/pytest-before.log`、`20261105T023844Z-T-001/pytest-after.log`

## 6. 规格变更（v2026.2）

- 重新导入：`{"import_id": "imp_1793846329", "project": "order-service", "spec_version": "2026.2", "documents": 13, "chunks": 56, "imported_at": "2026-11-05T02:38:49Z"}`
- T-002 上下文中的 `SPEC-PRM-002`：

```text
## SPEC-PRM-002 SAVE30 规则

券码 `SAVE30`：当商品小计大于或等于 150 元时减免 30 元；商品小计不足 150 元时抛出
`CouponNotApplicableError`。门槛判断只使用商品小计（SPEC-PRC-003），不得把运费计入。
```

- T-002 读取到的状态：

```text
[run] 20261105T023844Z-T-001 passed — passed：改动 src/order_service/money.py, src/order_service/promotion.py；目标测试 6 条，回归 13 条通过
[validation] 20261105T023844Z-T-001 passed — 目标测试 6/6 通过；回归 13 条通过
[todo] 20261105T023844Z-T-001 closed — T-002 规格变更复核（门槛 200→150）
```

## 7. 交付说明

- `runs/` 中的样例是离线复核记录；评审用 `--provider minimax` 重跑会生成真实的 MiniMax 请求/响应。
- `docs/technical-record.md` 可在每次运行后由 `scripts/make_record.py` 重新生成。
