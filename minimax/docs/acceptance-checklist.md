# 验收对照表（赛题 4.1 六项必做 + 4.2 验收方式）

评审按「提交的代码 + 运行记录 + 可复现命令」逐条核对。

## 一、4.1 六项必做（全部通过得 20 分）

### 1. 能运行

- 命令：`python -m venv .venv && pip install -r requirements.txt && python -m pytest -q`
- 期望：`32 passed`（回归 17 + 验收 15），不依赖任何外部服务即可跑通业务逻辑与全部测试。
- 手工验证业务入口：
  ```bash
  python -m settlement.cli settle  --order tests/fixtures/order_standard.json
  python -m settlement.cli preview --order tests/fixtures/order_excluded_gift.json --return 1,3
  ```
- 证据：`artifacts/runs/*/pytest.log`、`src/settlement/cli.py`。

### 2. MiniMax 实际参与

- 调用出口：`ctx/minimax.py::MiniMaxProvider.complete()`（`POST /chat/completions`，OpenAI 兼容）。
- 留痕：`artifacts/records/minimax_calls.jsonl`（提示词、响应、模型名、耗时、token、提示词指纹）。
- 与代码差异对应关系：
  - `artifacts/runs/<run_id>/response_1.md` —— 模型原始回复
  - `artifacts/runs/<run_id>/patch_attempt1.diff` —— 从回复中抽取的补丁（即实际应用的内容）
  - `artifacts/runs/<run_id>/working_tree.diff` —— 应用后的工作区差异
  - `git log --oneline` —— `feat(settlement): 实现 REQ-2026-011 …` 提交即该补丁的落地结果
- 说明：仓库内两次运行均为真实 MiniMax-M3 调用（`provider=minimax`）：011 共 4 轮（每轮 `prompt/response/patch`
  各一份，含失败反馈），012 一轮通过。`minimax_calls.jsonl` 里能直接看到每轮的 `model`、`latency_ms`、`usage`
  与响应全文；`--provider recorded` 仍保留作为无 Key 环境的回放路径。

### 3. 上下文正确

- 命令：`python -m ctx.cli context --task REQ-2026-011`
- 产物：`artifacts/runs/<run_id>/context.md` 明确给出
  - 规格条目：`SPEC-011-01 … 05`（来源 `spec/REQ-2026-011.md`、版本 `2026-11-07T20:00+08:00`）
  - 代码位置：`src/settlement/allocation.py::allocate` 等（附 chunk_id 与得分）
  - 验证测试：`tests/acceptance/test_req_2026_011.py::test_spec_011_01_…` 等
- 最新性验证：`REQ-2026-012` 的 `context.md` 版本为 `2026-11-08T10:30+08:00`，只出现 SPEC-011-06 新条款，
  旧版条款（v1 归档于 `spec/archive/`）未被召回 —— 说明是"重新导入后的最新内容"，不是历史对话残留。
- 召回明细：`artifacts/runs/<run_id>/retrieval.json`（每条 query → 命中 chunk_id → 分数）。

### 4. 数据真实可复现

- 入库：`python -m ctx.cli ingest`（seekdb 中真实建表、建 FULLTEXT 索引、写入 23 个文档 / 126 个片段）
- 核对：`python -m ctx.cli stats`（documents / chunks / by_kind / spec 版本）、`python -m ctx.cli search --query "…"`（真实检索结果）
- 检索结果与上下文包一致：`context.md` 中每个片段的 `chunk_id` 都能在 `search`/`retrieval.json` 中找到同一来源。
- 后端标注：`stats.backend` 为 `seekdb`（真实实例）或 `local-fts5`（离线兜底），不混淆。

### 5. 状态能够复用

- 保存：`ctx/state.py::PowerContextState.save()` → `POST /v1/memory/remember`（kind=task-state）
- 凭据：`artifacts/state/REQ-2026-011.json` 的 `powercontext.artifact.artifact_id`（例：`art_3ec3ae9286e5411088d4b06a5c4ce2a4`）
- 恢复：`python -m ctx.cli resume --task REQ-2026-012` 打印上游交接摘要 + PowerContext 召回命中
- 继承：`artifacts/runs/<run_id>/result.json` 的 `state_reused` 记录父任务、父 run_id、父补丁 sha、父验收计数；
  该信息同时写入 `context.md` 的「上游任务交接」段落，被模型实际使用。

### 6. 代码修改有效

| 阶段 | 回归集合（17 条） | 验收集合（15 条） |
| --- | --- | --- |
| 基线（`git tag` 前，补丁未应用） | 全绿 | 011 的 11 条全红（ImportError），012 的 4 条全红 |
| 应用 REQ-2026-011 补丁后 | 全绿（断言未改） | 11 条转绿，012 的 4 条仍红 |
| 应用 REQ-2026-012 补丁后 | 全绿 | 15 条全绿（合计 32 passed） |

- 回归集合断言未被改动：`git log --oneline -- tests/test_allocation.py tests/test_engine.py tests/test_service_cli.py`
  仅基线一次提交；验收用例在**实现开始前**校准过一次期望值（`861b065`，原期望漏算平台补贴 2500），实现过程中未再改动。
- 行为未变更的证据：`order_standard.json` 在修改前后行实付均为 `90000/18000/4500`、退款总额 `113700`（尾差为 0，新旧规则等价）。

## 二、4.2 验收方式（评审怎么跑）

1. **代码级**：`python -m pytest -q`（评审用自己的环境跑，不依赖提交者的临时状态）。
2. **链路级**：
   ```bash
   bash scripts/start_seekdb.sh && bash scripts/start_powercontext.sh
   export MINIMAX_API_KEY=... CTX_STORE_BACKEND=seekdb CTX_STATE_BACKEND=powercontext \
          POWERCONTEXT_URL=http://127.0.0.1:8077
   python -m ctx.cli ingest
   python -m ctx.cli run --task REQ-2026-011 --provider minimax
   python -m ctx.cli resume --task REQ-2026-012
   ```
3. **复核口径**：上述命令产出的 `artifacts/runs/<新 run_id>/` 下文件与仓库内已归档的运行记录结构一致，可逐字段对照。
   `python -m ctx.cli verify --task REQ-2026-011` 一次性输出复检结论（PASS/FAIL）。

## 三、补充说明

- 为了让"规格更新后模型使用最新内容"这件事可验证，规格文件保留了 v1 归档与 v2 变更记录（`spec/archive/` + 第 9 节变更记录）。
- 为了证明"重复执行不会重复改代码"，`ctx/patch.py` 对已应用的补丁返回 `already_applied`（幂等）；
  对同一任务再次执行 `python -m ctx.cli run --task REQ-2026-011` 即可看到该状态，不会重复改代码，也不会让测试变红。
