# 运行说明（RUNBOOK）

本文件是评审复现的唯一步骤来源。所有命令都在提交目录 `minimax/` 下执行。

## 0. 交付物

| 材料 | 位置 |
| --- | --- |
| 完整源码（上下文模块 + Agent 接入 + seekdb/PowerContext 适配） | `src/sddctx/` |
| 运行说明 | 本文件 |
| 验证样例（任务、规格、初始数据、步骤、预期、重置） | `docs/verification-sample.md`、`demo/`、`tasks/` |
| 代码与测试（修改前后快照、补丁、测试日志） | `runs/<run_id>/snapshots/`、`runs/<run_id>/diff.patch`、`runs/<run_id>/pytest-*.log` |
| 技术使用记录（MiniMax / 数据库 / PowerContext） | `docs/technical-record.md`、`runs/<run_id>/model/`、`runs/<run_id>/db-queries.json` |
| 一页技术说明 | `docs/technical-note.md` |

## 1. 环境要求

| 项 | 要求 | 已验证环境 |
| --- | --- | --- |
| 操作系统 | Linux x86_64（macOS 可用嵌入式 seekdb） | Ubuntu 24.04，内核 7.0.0 |
| Python | 3.11 – 3.13 | 3.12.3 |
| seekdb | `pylibseekdb>=1.4.0.post2`（嵌入式）或 OceanBase/seekdb MySQL 服务 | seekdb-v1.4.0.0 |
| PowerContext | `powercontext[server]==1.2.0` | 1.2.0（PyPI） |
| MiniMax | 可用的 API Key 与 Token Plan | `MiniMax-M3.1-Flash`（默认模型可替换） |

Windows 不能运行 `pylibseekdb`；请把 `SDD_STORE=mysql` 指向一台 seekdb/OceanBase（见 §3.2）。

## 2. 安装

```bash
cd minimax
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
export PYTHONPATH="$PWD/src"
```

依赖固定为：

- `powercontext[server]==1.2.0`：任务状态的保存与复用；
- `pylibseekdb>=1.4.0.post2`：嵌入式 seekdb，提供 MySQL 兼容 SQL 与全文索引；
- `pymysql`、`httpx`、`PyYAML`、`pytest`。

## 3. 配置

### 3.1 MiniMax

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `MINIMAX_API_KEY` | 是（真实生成补丁时） | 只从环境读取，不写入任何文件 |
| `MINIMAX_BASE_URL` | 否 | 默认 `https://api.minimaxi.com`；国际站填 `https://api.minimax.io` |
| `MINIMAX_MODEL` | 否 | 默认 `MiniMax-M3.1-Flash`，可改为账号可用的模型 |

```bash
export MINIMAX_API_KEY='...'
export MINIMAX_BASE_URL='https://api.minimaxi.com'
export MINIMAX_MODEL='MiniMax-M3.1-Flash'
```

### 3.2 数据库

默认使用嵌入式 seekdb，数据落在 `./.sdd/seekdb/`，无需账号。若改用服务端：

```bash
export SDD_STORE=mysql
export SDD_MYSQL_HOST=127.0.0.1
export SDD_MYSQL_PORT=2881
export SDD_MYSQL_USER=root
export SDD_MYSQL_PASSWORD=''
export SDD_MYSQL_DATABASE=sdd
```

服务端模式需要预先建库：`CREATE DATABASE IF NOT EXISTS sdd;`。

### 3.3 PowerContext

状态默认存放在 `./.sdd/state.db`（SQLite）。不需要单独启动服务：模块通过
`powercontext.builtin.runtime.open_builtin_runtime` 在进程内打开运行时，并把每次运行写入默认
Scope 的 Memory 工件。

## 4. 初始化数据

```bash
python -m sddctx init
python -m sddctx import
```

预期输出（数值以实际语料为准）：

```json
{"db": ".../minimax/.sdd/seekdb", "schema": "ready"}
{"import_id": "...", "project": "order-service", "spec_version": "2026.1",
 "documents": 13, "chunks": 56, "imported_at": "..."}
```

`import` 会扫描 `demo/order-service/specs/*.md`（按 `## SPEC-…` 条款切分）、`src/`（按顶层
函数/类切分，另加模块前导块）和 `tests/`（按 `test_*` 函数切分），写入：

- `sdd_document`：文件级元数据（路径、sha256、规格版本）；
- `sdd_chunk`：可引用片段，含 `path/start_line/end_line/symbol/tokens`，并建立
  `FULLTEXT INDEX … WITH PARSER ik`；
- `sdd_import`：导入批次与规格版本。

## 5. 验证样例 A：T-001 实现 SAVE30 + HALF_UP

任务定义见 `tasks/T-001.yaml`，业务工程见 `demo/order-service/`。

### 5.1 先看上下文（数据库检索）

```bash
python -m sddctx context T-001 --json
```

预期：`spec` 含 `SPEC-PRM-002 / SPEC-PRM-003 / SPEC-PRM-004 / SPEC-PRC-002`；`code` 含
`order_service.promotion.apply_coupon`、`order_service.service.quote`、
`order_service.money.round_money`；`tests` 含 `tests/test_coupon.py` 的 6 条目标测试；
每条都带 `path:start-end` 来源。`db_rows > 0`、`tokens <= budget`。

### 5.2 用 MiniMax 完成修改

```bash
python -m sddctx run T-001 --provider minimax
```

预期（节选）：

```json
{
  "status": "passed",
  "verification": {
    "target_failed_before": ["tests/test_coupon.py::test_save30_applies_at_threshold", "..."],
    "target_passed_after": ["tests/test_coupon.py::test_save30_applies_at_threshold", "..."],
    "no_new_failures": true,
    "tests_untouched": true,
    "ok": true
  },
  "changes": [{"path": "src/order_service/money.py"}, {"path": "src/order_service/promotion.py"}]
}
```

产物在 `runs/<run_id>/`：`context.json|md`、`prompt.md`、`model/`（请求/响应原文）、
`diff.patch`、`snapshots/before|after/`、`pytest-before.log`、`pytest-after.log`、
`db-queries.json`、`result.json`。

### 5.3 没有 Key 时的离线复核

离线模式不调用模型，只应用审阅过的补丁，用于验证「检索 → 打补丁 → 测试 → 存状态」链路：

```bash
python -m sddctx run T-001 --provider manual --patch patches/T-001.patch
```

`runs/<run_id>/model/manual-source.json` 会明确记录 `"provider": "manual"`，不得把它当作
MiniMax 调用记录。

## 6. 验证样例 B：规格变更后重新导入

> 顺序很重要：T-001 的目标测试按 v2026.1 的 200 元门槛断言。请先完成 §5，再执行本节；
> 若要先重跑 T-001，请先 `python -m sddctx import`（不带 overlay）恢复 v2026.1。

```bash
python -m sddctx import --spec-version 2026.2 \
  --spec-overlay demo/spec-updates/2026.2/promotion.md
python -m sddctx context T-002 --json
```

预期：`SPEC-PRM-002` 的文本变为「大于或等于 150 元」，不再出现「200 元」；`state` 中出现
T-001 的 `run / validation / todo` 记录。规格文件本身没有被改写，只是本次导入使用 overlay。

## 7. 状态复用（重启进程后）

每个命令都是独立进程，上一节的 `context T-002` 已经证明跨进程读取。可再显式检查：

```bash
python -m sddctx status T-001
```

预期输出包含三行记录：`[run] T-001 passed`、`[validation] T-001 passed`、`[todo] T-001 closed`。
续跑任务时，`context` 的 `state` 字段会再次带上这些记录并进入 MiniMax 提示词。

## 8. 测试

```bash
# 上下文模块自身
python -m pytest -q

# 验证样例工程（修改前应有 6 条失败）
cd demo/order-service && python -m pytest -q && cd ../..
```

模块测试共 25 条，覆盖语料切分、来源行号、补丁解析/应用、禁改测试、检索配额与预算、
提示词渲染、环境配置与 MiniMax 客户端重试/错误处理。

## 9. 重置

```bash
python -m sddctx reset      # 删除 .sdd/seekdb 与 .sdd/state.db*
rm -rf runs                 # 如需连同历史运行产物一起清理
```

`demo/order-service/` 从未被修改：每次 `run` 都把它复制到 `runs/<run_id>/workspace/` 后再打补丁，
所以重置不需要恢复业务工程。

## 10. 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| `pylibseekdb is not installed` | 平台不是 Linux/macOS，改为 `SDD_STORE=mysql` 指向 seekdb 服务 |
| `MINIMAX_API_KEY is not set` | 导出 Key，或改用 `--provider manual --patch ...` |
| `MiniMax error 1004` | Key 无效；`1008` 表示余额不足 |
| `MiniMax returned an empty reply` | 推理模型输出被截断，增大 `max_completion_tokens` 或重试 |
| `hunk … does not match` | 模型补丁与当前文件不一致，重新运行或人工审阅 `model-reply.md` |
| `PowerContext is not installed` | `pip install 'powercontext[server]==1.2.0'` |

## 11. 已知限制

1. 嵌入式 seekdb 仅支持 Linux/macOS；Windows 走 MySQL 服务端模式。
2. 检索以 seekdb 全文索引 + 精确来源匹配为主；MiniMax embedding 通道未纳入默认路径。
3. MiniMax 输出按 unified diff 解析；若模型返回说明文字，`extract_diff` 会取第一个合法 diff。
4. `runs/` 中的样例运行使用 `manual` provider 或真实 MiniMax，取决于交付时的记录（见
   `docs/technical-record.md`）；评审重新执行 `--provider minimax` 会生成新的记录与补丁。
