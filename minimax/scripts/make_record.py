"""Generate docs/technical-record.md from the latest run artifacts.

Run after ``python -m sddctx run ...``: ``python scripts/make_record.py``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def latest_run() -> Path:
    candidates = sorted(
        (path for path in (ROOT / "runs").glob("*") if (path / "result.json").is_file()),
        key=lambda path: path.name,
    )
    if not candidates:
        raise SystemExit("no run artifacts found under runs/")
    return candidates[-1]


def main() -> None:
    run = latest_run()
    result = load(run / "result.json", {})
    context = load(run / "context.json", {})
    queries = load(run / "db-queries.json", [])
    verification = result.get("verification", {})
    prompt = (run / "prompt.md").read_text(encoding="utf-8") if (run / "prompt.md").is_file() else ""
    diff = (run / "diff.patch").read_text(encoding="utf-8") if (run / "diff.patch").is_file() else ""
    status = (run / "powercontext-status.txt").read_text(encoding="utf-8") if (run / "powercontext-status.txt").is_file() else ""
    spec_dir = ROOT / "runs" / "spec-update-v2026.2"
    spec_context = load(spec_dir / "context-T-002.json", {})
    stats = load(spec_dir / "stats-2026.2.json", {})
    import_summary = load(spec_dir / "import-2026.2.json", {})

    manual = result.get("provider") == "manual"
    similarity = next((h["text"] for h in spec_context.get("spec", []) if h["symbol"] == "SPEC-PRM-002"), "")
    match_query = next((q for q in queries if "MATCH(text)" in str(q.get("sql_preview"))), None)
    rows = match_query.get("rows") if match_query else 0
    elapsed = match_query.get("elapsed_ms") if match_query else 0

    lines: list[str] = []
    lines += [
        "# 技术使用记录",
        "",
        f"> 由 `scripts/make_record.py` 从 `{run.name}/` 生成；重新执行 `python -m sddctx run ...` 后会得到新的运行目录。",
        "",
        "## 0. 记录范围",
        "",
        f"- 运行 ID：`{result.get('run_id', run.name)}`",
        f"- 运行状态：`{result.get('status', '?')}`",
        f"- 模型提供方：`{result.get('provider', '?')}` / `{result.get('model', '?')}`",
        "- 说明：本次提交的 `runs/` 样例运行"
        + ("使用 **manual** 提供方（审阅过的补丁），**没有调用 MiniMax**；MiniMax 调用链由单元测试和本地回环 stub 验证。" if manual else "使用 **MiniMax** 实际生成补丁。"),
        "",
        "## 1. MiniMax 使用记录",
        "",
    ]

    if manual:
        lines += [
            "本机交付时没有可用的 MiniMax API Key，因此 `runs/` 中的样例用 `--provider manual` 完成，",
            "产物中 `model/manual-source.json` 明确记录 `provider=manual`，不冒充模型调用。",
            "真实运行方式：",
            "",
            "```bash",
            "export MINIMAX_API_KEY=...",
            "export MINIMAX_BASE_URL=https://api.minimaxi.com   # 国际站用 https://api.minimax.io",
            "python -m sddctx reset && python -m sddctx init && python -m sddctx import",
            "python -m sddctx run T-001 --provider minimax",
            "python scripts/make_record.py",
            "```",
            "",
            "### 1.1 已完成的调用链验证",
            "",
            "- 单元测试 `tests/test_minimax.py`：请求体、`base_resp` 业务错误、5xx 重试、空回复拒绝、manual 标记。",
            "- 端到端 stub：`scripts/minimax_stub.py` 在本地回环模拟 `/v1/text/chatcompletion_v2`，",
            "  `MINIMAX_BASE_URL=http://127.0.0.1:8787 MINIMAX_API_KEY=stub python -m sddctx run T-001 --provider minimax`",
            "  能完成「HTTP 请求 → 响应解析 → diff 应用 → 测试通过 → 状态写入」，产物包含 `minimax-request-01.json`、",
            "  `minimax-response-01.json`。该 stub 运行不写入 `runs/`，也不作为 MiniMax 参与证据。",
            "",
        ]
    else:
        completion = load(run / "model" / "minimax-response-01.json", {})
        usage = completion.get("usage", {})
        lines += [
            "| 项 | 值 |",
            "| --- | --- |",
            f"| 端点 | `POST {result.get('model', '')}` / v1/text/chatcompletion_v2 |",
            f"| 模型 | `{result.get('model', '?')}` |",
            f"| 请求 ID | `{completion.get('id', '?')}` |",
            f"| 用量 | `{json.dumps(usage, ensure_ascii=False)}` |",
            f"| 提示词 SHA-256 | `{sha(prompt)}` |",
            f"| 补丁 SHA-256 | `{sha(diff)}` |",
            "",
        ]

    lines += [
        "## 2. 上下文（检索结果）",
        "",
        f"- 查询词：`{context.get('query', '')}`",
        f"- 候选行数：{context.get('db_rows', '?')}；选中 {len(context.get('spec', [])) + len(context.get('code', [])) + len(context.get('tests', []))} 条；",
        f"  {context.get('tokens', '?')} / {context.get('budget_tokens', '?')} tokens；截断：{context.get('truncated', '?')}",
        "",
        "| 类别 | 来源 |",
        "| --- | --- |",
    ]
    for hit in context.get("spec", []):
        lines.append(f"| 规格 `{hit['symbol']}` | `{hit['source']}` |")
    for hit in context.get("code", []):
        lines.append(f"| 代码 `{hit['symbol']}` | `{hit['source']}` |")
    for hit in context.get("tests", []):
        lines.append(f"| 测试 `{hit['symbol']}` | `{hit['source']}` |")

    lines += [
        "",
        "## 3. seekdb / OceanBase 使用记录",
        "",
        f"- 存储：嵌入式 seekdb（`pylibseekdb`），版本 `5.7.25-OceanBase seekdb-v1.4.0.0`",
        f"- 语料：`sdd_document` {stats.get('documents', '?')} 行，`sdd_chunk` {stats.get('chunks_by_kind', {})}",
        f"- 最近导入：`{json.dumps(import_summary, ensure_ascii=False)}`",
        f"- 全文检索：`MATCH(text) AGAINST(...)` 命中 {rows} 行，耗时 {elapsed} ms（`db-queries.json` 第 {queries.index(match_query) + 1 if match_query else '?'} 条）",
        "",
        "关键 SQL（截断自 `db-queries.json`）：",
        "",
        "```sql",
    ]
    if match_query:
        lines.append(str(match_query["sql_preview"]).replace("\n", " ")[:400])
    lines += [
        "```",
        "",
        "精确来源查询（规格编号/符号/路径）：",
        "",
        "```sql",
        "SELECT chunk_id, doc_id, kind, symbol, path, start_line, end_line, tokens, text, 0 AS score",
        "FROM sdd_chunk WHERE project = 'order-service' AND (symbol IN ('SPEC-PRM-002', ...) OR path IN (...))",
        "```",
        "",
        "## 4. PowerContext 使用记录",
        "",
        "运行时：`powercontext[server]==1.2.0`，进程内 `open_builtin_runtime`，默认 Scope 的 Memory 工件，",
        "数据库 `.sdd/state.db`（SQLite）。每次运行追加 `run` / `validation` / `todo` 三类条目。",
        "",
        "`python -m sddctx status T-001` 在独立进程中读回的内容：",
        "",
        "```text",
        status.strip()[:2000],
        "```",
        "",
        "## 5. 代码差异与测试",
        "",
        "| 文件 | 新增 | 删除 |",
        "| --- | ---: | ---: |",
    ]
    for change in result.get("changes", []):
        lines.append(f"| `{change['path']}` | {change['added']} | {change['removed']} |")
    lines += [
        "",
        f"- 目标测试：{len(verification.get('target_tests', []))} 条；修改前失败 {len(verification.get('target_failed_before', []))} 条；"
        f"修改后通过 {len(verification.get('target_passed_after', []))} 条",
        f"- 回归通过：{len(verification.get('regression_passed', []))} 条；无新增失败：{verification.get('no_new_failures')}",
        f"- 测试目录 SHA-256：修改前 `{str(verification.get('tests_tree_sha256_before'))[:16]}…`，修改后 `{str(verification.get('tests_tree_sha256_after'))[:16]}…`；未改动：{verification.get('tests_untouched')}",
        f"- 测试日志：`{run.name}/pytest-before.log`、`{run.name}/pytest-after.log`",
        "",
        "## 6. 规格变更（v2026.2）",
        "",
        f"- 重新导入：`{json.dumps(import_summary, ensure_ascii=False)}`",
        f"- T-002 上下文中的 `SPEC-PRM-002`：",
        "",
        "```text",
        similarity.strip(),
        "```",
        "",
        "- T-002 读取到的状态：",
        "",
        "```text",
        "\n".join(
            f"[{record.get('kind')}] {record.get('run_id')} {record.get('status')} — {record.get('summary')}"
            for record in spec_context.get("state", [])
        ),
        "```",
        "",
        "## 7. 交付说明",
        "",
        "- `runs/` 中的样例是离线复核记录；评审用 `--provider minimax` 重跑会生成真实的 MiniMax 请求/响应。",
        "- `docs/technical-record.md` 可在每次运行后由 `scripts/make_record.py` 重新生成。",
    ]

    target = ROOT / "docs" / "technical-record.md"
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
