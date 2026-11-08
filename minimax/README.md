# MiniMax × seekdb：订单退款结算的上下文工程闭环

> 2026 OceanBase 数据库大赛 · MiniMax 赛题（20 分）参赛作品
> 业务工程：`settlement` 订单退款结算引擎（Python 3.11+，纯标准库实现核心逻辑）
> 技术三件套：**MiniMax**（代码修改与调试）+ **seekdb/OceanBase**（工程资料存储与全文检索）+ **PowerContext**（任务状态保存与跨会话复用）

---

## 1. 方案一句话

把「需求规格 → 目标文件 → 验证测试 → 上游交接状态」从 seekdb 全文索引里检索成一份**可复核的上下文包**，交给 **MiniMax-M3** 产出改动；
改动落地后跑整套 pytest，结果与证据写回 PowerContext。整条链路每一步都留痕，可离线重放。

两个真实任务（同一工程、两次会话，**模型为真实 MiniMax-M3 API 调用**）：

| 任务 | 内容 | 结果 | 运行记录 |
| --- | --- | --- | --- |
| `REQ-2026-011` | 类目排除分摊、尾差轮转、运费退还判定、负金额校验、退款预览 | 3 轮迭代收敛，28 tests passed | `artifacts/runs/20261108T221044-REQ-2026-011/` |
| `REQ-2026-012` | 规格 v2 追加项：退款结果暴露剩余实付 | 继承 011 状态，1 轮通过，32 tests passed | `artifacts/runs/20261108T221231-REQ-2026-012/` |

> 交付形态：`--mode files`（整文件）为主，`--mode diff`（unified diff）保留。
> 011 的 3 轮分别是「整文件写入 → 测试失败 → 带着 pytest 尾部反馈重写」，每次反馈都落在 `prompt_attempt*.md` 里，可逐步复盘模型为何改错、又如何改对。

> 表中数字是**运行时**该任务验收命令的实测值（011：17 回归 + 11 验收；012：17 回归 + 15 验收）。
> 仓库当前全量为 40 项（再加 3 项上下文 CLI 冒烟 `tests/test_ctx_cli.py`、5 项补丁处理单测 `tests/test_ctx_patch.py`）。

---

## 2. 目录结构

```
spec/REQ-2026-011.md          需求规格 v2（含 SPEC-011-01 ~ 06 条款、验收标准、错误码、变更记录）
spec/archive/REQ-2026-011.v1.md  v1 归档（不参与检索，用于证明"规格更新后以最新版为准"）
tasks/REQ-2026-011.json       任务卡（spec_codes / target_files / 验收命令）
tasks/REQ-2026-012.json       任务卡（parent_task 指向 011，用于状态继承）

src/settlement/               业务工程（models / allocation / engine / validators / service / cli）
tests/                        回归 17 条 + 验收 15 条（tests/acceptance/）+ 上下文 CLI 冒烟 3 条 + 补丁处理 5 条
tests/fixtures/               订单样例（标准单、类目排除单、尾差单、全额抵扣单、负金额单）

ctx/store.py                  工程资料存储与全文检索：SeekdbStore（FULLTEXT+ngram）/ LocalFtsStore（FTS5 兜底）
ctx/ingest.py                 文档/代码/测试分块与元数据抽取（SPEC 编码、符号名、版本）
ctx/retrieve.py               任务级多路召回（规格条目 / 代码位置 / 验证测试）
ctx/package.py                上下文包（可渲染成 Markdown 喂给模型，也是可复核产物）
ctx/minimax.py                MiniMax 调用出口 + 调用留痕 + 补丁抽取
ctx/patch.py                  补丁校验与幂等应用（git apply）
ctx/state.py                  任务状态：PowerContextState（REST）/ JsonState（离线兜底）
ctx/runner.py                 任务闭环：检索→生成→应用→回归→留痕→落状态
ctx/cli.py                    统一命令行入口

artifacts/runs/<run_id>/      每次运行的证据：context.md/json、prompt、response、patch、pytest.log、result.json
                              011 运行：4 份 prompt/response/patch + 每次的 pytest.log 尾部
artifacts/records/minimax_calls.jsonl  逐条模型调用留痕（提示词、响应、模型、耗时、token）
artifacts/state/<task>.json   状态快照（与 PowerContext 中的记忆条目同源）
artifacts/recorded/           无 Key 时可回放的模型响应（演示/回归用，README 第 9 节说明）

docs/technical-note.md        技术说明（方案、检索打分、状态模型、取舍）
docs/acceptance-checklist.md  六项必做 + 4.2 验收方式的逐条对照
scripts/                      启动 seekdb / PowerContext、一键跑通
```

---

## 3. 快速开始

### 3.1 只跑业务工程与测试（无需任何外部服务）

```bash
python -m venv .venv && pip install -r requirements.txt
python -m pytest -q                 # 期望 32 passed
python -m settlement.cli settle --order tests/fixtures/order_standard.json
python -m settlement.cli preview --order tests/fixtures/order_excluded_gift.json --return 1,3
```

### 3.2 起 seekdb（工程资料存储 + 全文检索，赛题必做）

```bash
bash scripts/start_seekdb.sh     # 等价于 docker run -d -p 2881:2881 oceanbase/seekdb
export CTX_STORE_BACKEND=seekdb  # 连不上会自动回退本地 FTS5，并在 stats 里标注 backend
python -m ctx.cli stats
```

`SEEKDB_*` 环境变量见 `.env.example`。首次连接会自动 `CREATE DATABASE IF NOT EXISTS ctx` 并建表建索引。

### 3.3 起 PowerContext（任务状态与交接，赛题必做）

```bash
bash scripts/start_powercontext.sh      # 127.0.0.1:8077，SQLite 内置运行时，无需模型
export CTX_STATE_BACKEND=powercontext POWERCONTEXT_URL=http://127.0.0.1:8077
```

### 3.4 跑一次完整任务闭环

```bash
# 真实调用 MiniMax（需要 Key）
export MINIMAX_API_KEY=sk-...
python -m ctx.cli ingest                # 工程资料入库
python -m ctx.cli run --task REQ-2026-011 --provider minimax
python -m ctx.cli resume --task REQ-2026-011
```

没有 Key 也能验证整条链路：`--provider recorded` 会回放 `artifacts/recorded/REQ-2026-011/response_1.md`
（归档的模型响应），其余步骤（检索、应用补丁、回归、落状态、留痕）完全一致——见第 9 节说明。

一键串联：`bash scripts/run_all.sh`。

---

## 4. 上下文工程怎么做的（对应赛题 2.3）

1. **分块**：Markdown 按 `#/##/###` 切到小节（SPEC 条款各自独立成块）；Python 按 AST 切到函数/类，`symbol` 记录 `模块.类.函数`；
   JSON/其他整文件成块。每块抽取 `spec_codes`（`SPEC-/REQ-/AC-/BACKLOG-` 编号）与文档版本（`- 版本：`）。
2. **入库**：同一 `doc_id` 覆盖式写入（先删后插），因此规格从 v1 升到 v2 后，检索不会再返回旧条款。
   规格 v1 已移入 `spec/archive/` 且被 ingest 排除，可对照验证。
3. **召回**：一个任务三路召回——规格条目（按任务卡 `spec_codes` 逐条定位）、代码位置（按 `target_files` 限定 + 语义检索）、
   验证测试（按 SPEC 编码召回带同名 docstring 的用例）。
4. **重排**：全文召回后用可解释打分重排 —— 正文覆盖度 ×3.0 + 标题覆盖 ×1.0 + 词频 ×0.1 + 规格编码精确命中 +6.0 + 结构信号；
   每次召回的打分明细写入 `artifacts/runs/<run_id>/retrieval.json`。
5. **上下文包**：`context.md` 明确列出「使用了哪些规格条目（编码/来源/版本）、哪些代码函数、哪些测试、上游交接状态、召回留痕」，
   既是提示词主体，也是评审可直接对照的产物。
6. **最新性**：包内 `规格版本` 取自索引里的文档版本；`REQ-2026-012` 的运行记录显示版本为 `2026-11-08T10:30+08:00`（v2），
   且只召回 SPEC-011-06 的新条款，说明模型使用的是重新导入后的最新内容，而非历史上下文。

---

## 5. MiniMax 怎么用的（对应赛题 2.2 / 4.1「MiniMax 实际参与」）

- **唯一出口**：`ctx/minimax.py::MiniMaxProvider.complete()`，OpenAI 兼容协议 `POST {MINIMAX_BASE_URL}/chat/completions`，
  默认 `MINIMAX_MODEL=MiniMax-M3`（当前账户可用 `MiniMax-M3` / `MiniMax-M3.1-Flash`，M2 系列已下线），
  可用 `--provider minimax` 或 `ctx.cli ask --question ...` 直接调用。
- **任务链路**：检索出的上下文包 + 规格约束 → MiniMax 产出 unified diff → `git apply` → `pytest -q` → 失败则带测试日志重试（最多 3 次）。
- **留痕**：每次调用写 `artifacts/records/minimax_calls.jsonl`（system/user 提示词、响应全文、模型、耗时、token 用量、提示词指纹），
  同一次运行的原始响应另存 `artifacts/runs/<run_id>/response_<n>.md`，抽取的补丁存 `patch_attempt<n>.diff`，
  与最终提交的代码差异（`git show` / `working_tree.diff`）一一对应。
- **约束**：系统提示词禁止修改测试、禁止改公开签名、金额必须整数分；`runner.apply_patch` 只接受可 `git apply` 的补丁，
  避免"模型自述改动"与真实代码不符。

---

## 6. seekdb / OceanBase 怎么用的（对应赛题 2.2）

`ctx/store.py::SeekdbStore`（MySQL 协议，pymysql）：

| 表 | 关键点 |
| --- | --- |
| `ctx_doc` | 文档级元数据：路径、类型、版本、内容 sha256、更新时间 |
| `ctx_chunk` | 片段级：所属文档、标题路径、符号名、SPEC 编码、token 串、原文；`FULLTEXT INDEX ft_ctx_chunk_tokens(tokens) WITH PARSER ngram` |

检索：`MATCH(tokens) AGAINST(? )` 取候选 → 覆盖度重排 → 返回证据片段（`chunk_id` 贯穿上下文包与运行记录）。
若实例不支持 ngram 解析器，自动退化为普通 FULLTEXT，再退化为多词 `LIKE`（`stats.fts_mode` 会标注实际模式），
保证"检索一定可用"，同时不掩盖降级事实。

`python -m ctx.cli search --query "尾差 分摊 类目排除" --k 5` 可直接观察检索效果与打分明细。

---

## 7. PowerContext 怎么用的（对应赛题 2.4「状态能够复用」）

`ctx/state.py::PowerContextState`（REST，PowerContext 1.5）：

| 动作 | 接口 | 内容 |
| --- | --- | --- |
| 保存 | `POST /v1/memory/remember` | `kind=task-state`，text 为状态摘要（任务、规格版本、补丁 sha、测试计数、已完成/待办/遗留、证据片段、run 目录、快照路径） |
| 召回 | `POST /v1/atomic-memory/search` | 新会话按任务号召回历史状态（`ctx.cli resume --task ...` 会打印） |
| 一致性 | 本地快照 `artifacts/state/<task>.json` | 与记忆条目同源，含完整证据列表；`status.powercontext.artifact.artifact_id` 是落库凭据 |

跨会话演示：`REQ-2026-012` 的任务卡带 `parent_task: REQ-2026-011`，运行时会从 PowerContext 读回 011 的交接状态，
写进上下文包的「上游任务交接」段落（见 `artifacts/runs/20261108T221231-REQ-2026-012/context.md` 第 4 节），
`result.json.state_reused` 记录继承来源（父任务、父 run_id、父补丁 sha、父验收计数）。

---

## 8. 验收对照（详见 `docs/acceptance-checklist.md`）

| 验收项 | 证据 |
| --- | --- |
| 能运行 | `python -m pytest -q` → 40 passed；CLI `settle/preview/refund` 可直接跑 |
| MiniMax 实际参与 | `artifacts/records/minimax_calls.jsonl`（真实 API：model/耗时/token/响应全文）+ `runs/<id>/response_*.md` + `patch_attempt*.diff` 与 `git show` 逐条对应 |
| 上下文正确 | `runs/<id>/context.md`（规格编码/版本、代码符号、测试名）+ `retrieval.json`（召回与打分） |
| 数据真实可复现 | `ctx.cli stats` / `search`（seekdb 中真实写入与检索）；`ingest` 可随时重建 |
| 状态能够复用 | `ctx.cli resume --task REQ-2026-012`、`result.json.state_reused`、PowerContext artifact id |
| 代码修改有效 | 修改前后回归全绿（17 条回归不变），验收用例从红到绿；`docs/acceptance-checklist.md` 有前后对照 |

---

## 9. 已知限制与说明

1. **模型是真实调用，存储是本地兜底**：`artifacts/runs/*` 的两次运行均为 **MiniMax-M3 真实 API**（`provider=minimax`，
   记录见 `artifacts/records/minimax_calls.jsonl`）。存储侧因本机 Docker 的 WSL2 后端被安全策略阻断，暂用
   `LocalFtsStore`（SQLite FTS5）承载同一套 schema 与检索逻辑，`stats.backend` 会如实标注 `local-fts5`；
   换到可用 seekdb 后 `CTX_STORE_BACKEND=seekdb python -m ctx.cli ingest` 即可切换，检索代码不变。
   离线场景仍可用 `--provider recorded` 回放归档响应，便于无 Key 机器复现。
2. **PowerContext 单条记忆上限 8192 字节**：因此写入记忆的是「状态摘要 + 快照指针」，完整 payload 在 `artifacts/state/`，
   二者用 `snapshot_path` 关联，避免截断导致状态失真。
3. **Windows 上 seekdb 需 Docker**：`scripts/start_seekdb.sh` 使用命名卷（官方对 Windows 的要求）。
4. **`tests/acceptance/` 在实现前是红的**：这是任务材料的一部分（TDD 口径），
   `pytest -m "not acceptance"` 可在基线状态单独验证回归集合。

---

## 10. 常用命令速查

```bash
python -m ctx.cli doctor                                  # 环境体检：三件套各自是否可用 + 修复提示
python -m ctx.cli stats                                   # 索引统计 + 规格文档版本
python -m ctx.cli search --query "SPEC-011-02 尾差" -k 5   # 全文检索（含打分明细）
python -m ctx.cli context --task REQ-2026-011              # 只生成上下文包，不调模型
python -m ctx.cli run --task REQ-2026-011 --provider minimax
python -m ctx.cli resume --task REQ-2026-012               # 新会话恢复（含 PowerContext 召回）
python -m ctx.cli verify --task REQ-2026-011               # 提交前自检
python -m ctx.cli ask --question "尾差为什么要逐行 1 分？"   # 直接问模型（同样留痕）
```
