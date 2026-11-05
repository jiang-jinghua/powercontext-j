# 一页技术说明

## 1. 模块结构

```
sddctx
├── corpus.py     规格/代码/测试 → 结构化片段（条款、符号、测试函数 + 行号）
├── store.py      片段写入 seekdb；全文检索与精确来源查询；记录每条 SQL
├── retrieve.py   任务 → 查询词/来源约束 → 打分、配额、预算 → ContextPack
├── context.py    渲染人读 Markdown、MiniMax 提示词、机器可读 JSON
├── minimax.py    /v1/text/chatcompletion_v2 客户端（重试、原始请求/响应留痕）
├── patch.py      unified diff 解析与严格应用（禁止改 tests/，路径越界拒绝）
├── state.py      PowerContext Memory：写入运行/验证/待办，跨进程读取
└── pipeline.py   编排：检索 → 提示词 → 补丁 → 测试 → 留痕 → 存状态
```

调用入口是 CLI（`python -m sddctx …`）；同一套函数也可被 HTTP/MCP/Agent 插件直接调用，
输入是任务 YAML，输出是带来源的上下文包与运行结果。

## 2. 上下文选择方式

1. **结构化切分**：规格按 `## SPEC-…` 条款切分；代码用 `ast` 按顶层函数/类切分（额外保留
   模块前导块）；测试按 `test_*` 函数切分。每块都带 `path:start_line-end_line`、符号名和
   token 估算，来源可逐条回溯到文件行。
2. **数据库内检索**：seekdb 表 `sdd_chunk` 上建 `FULLTEXT INDEX … WITH PARSER ik`，
   查询用 `MATCH(text) AGAINST(...)` 排序；同时对任务声明的规格编号、符号名、文件路径做
   `IN` 精确查询。两路结果合并，检索在数据库完成，不是把全表拉到 Python 里算。
3. **任务级加权**：全文得分归一化后，命中任务声明的 `spec_refs` 加 3、`symbols` 加 2.5、
   `paths` 加 2.0、目标测试加 1.5；测试正文引用待改符号再补 0.4。
4. **配额 + 预算**：先保证规格/代码/测试各至少 2 条，并强制包含任务声明的必需来源；再按得分
   填满到 12 条或 3600 token。超出预算时标记 `truncated`，不静默截断来源。
5. **规格演进**：`import --spec-overlay` 用同名文件替换一个规格后再切分/入库；检索自然返回
   新条款文本，不需要改代码或清缓存。

## 3. 技术分工

| 技术 | 承担的工作 | 不可替代的原因 |
| --- | --- | --- |
| MiniMax | 阅读上下文包，输出 unified diff | 代码修改由模型完成，提示词包含规格原文、现有实现与验证测试 |
| seekdb | 存储 56 个工程片段并执行全文/精确查询 | 上下文中的 `spec/code/tests` 全部来自数据库返回行；SQL 与耗时被记录 |
| PowerContext | 保存运行、验证结果、待办；下一次运行读取 | 跨进程恢复：`status` 与 T-002 的 `state` 字段直接来自 Memory 工件 |

## 4. 验证结果

- T-001：6 条目标测试在修改前全部失败，应用 MiniMax 补丁后全部通过；13 条回归测试始终通过；
  `tests/` 目录逐字节未变。
- T-002：重新导入 v2026.2 后，`SPEC-PRM-002` 返回 150 元门槛；上下文同时带出 T-001 的
  `run/validation/todo` 记录。
- 数据库：`sdd_document` 13 行、`sdd_chunk` 56 行；每次检索的 SQL、命中行数、耗时见
  `runs/<run_id>/db-queries.json`。

## 5. 取舍

- 选择嵌入式 seekdb 而不是另起数据库服务：评审只需 `pip install`，无账号、无端口；服务端
  模式保留为 `SDD_STORE=mysql`。
- 选择「结构化片段 + 全文检索」而非纯向量：本任务的关键是条款编号、符号名、测试名这些
  精确锚点，全文检索更可解释；向量通道留作后续扩展。
- PowerContext 用 1.2.0 的进程内运行时：与赛题冻结版本一致，状态落在 SQLite，避免评审再启动
  一个 HTTP 服务。
