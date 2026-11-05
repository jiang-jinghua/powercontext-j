# 必做项与证据对照

评审从干净环境开始，按 `RUNBOOK.md` 依次验证。下表把 6 项必做项对应到可执行命令与产物。

| # | 必做项 | 验证命令 | 证据 |
| --- | --- | --- | --- |
| 1 | 能够运行 | `python -m sddctx reset && python -m sddctx init && python -m sddctx import && python -m sddctx run T-001 --provider minimax` | `runs/<run_id>/result.json`、`pytest-before.log`、`pytest-after.log`；不依赖仓库外文件，`demo/` 每次复制到运行目录 |
| 2 | MiniMax 实际参与 | 同上（`--provider minimax`） | `runs/<run_id>/prompt.md`（提供的上下文）、`model/minimax-request-*.json`、`model/minimax-response-*.json`、`diff.patch`；`docs/technical-record.md` 记录任务、模型、耗时与补丁对应关系 |
| 3 | 上下文正确 | `python -m sddctx context T-001 --json`；`python -m sddctx context T-002 --json` | `context.json` 中 `spec/code/tests` 的 `source` 均在仓库内存在；T-002 的 `SPEC-PRM-002` 为 150 元 |
| 4 | 数据库实际参与 | `python -m sddctx stats`；检索日志 | `sdd_document`/`sdd_chunk` 行数；`runs/<run_id>/db-queries.json` 中 `MATCH … AGAINST` 与 `IN` 查询及命中行数 |
| 5 | 状态能够复用 | 先 `run T-001`，再新进程 `status T-001`；`context T-002 --json` 的 `state` | `runs/<run_id>/` 之外的 `.sdd/state.db`；`status` 输出三条记录；`context T-002` 的 `state` 数组 |
| 6 | 代码修改有效 | `run T-001` 的 `verification` | `target_failed_before` 覆盖 6 条目标测试；`target_passed_after` 同样 6 条；`no_new_failures=true`；`tests_untouched=true` |

## 复核要点

- **测试集未改**：补丁应用器拒绝任何 `tests/` 路径；`runs/*/snapshots/` 只保存 `src/` 下被改文件；
  `tests_untouched` 为 `true`。
- **来源真实存在**：上下文中的 `source` 形如 `specs/promotion.md:12-15`，行号对应条款标题；
  `test_corpus.py::test_spec_provenance_points_at_the_heading` 对此有回归测试。
- **规格更新生效**：overlay 只替换同名规格文件，入库后按新文本切分；不需要重启或清缓存。
- **状态跨进程**：每条 CLI 命令都是独立进程，`status` 与 `context` 均从 SQLite 中的
  PowerContext Memory 工件读取。
- **离线模式**：`--provider manual` 的产物明确标记 `provider=manual`，不冒充 MiniMax 调用。
