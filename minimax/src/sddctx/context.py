"""Render a context pack for humans, for the model prompt, and for storage."""

from __future__ import annotations

import json

from .retrieve import ContextPack


def _block(title: str, source: str, text: str) -> str:
    return f"### {title}\n\n来源：`{source}`\n\n```\n{text.rstrip()}\n```\n"


def render_markdown(pack: ContextPack) -> str:
    """Human-readable context: what applies, what to edit, what proves it."""
    lines = [
        f"# 任务 {pack.task.id}：{pack.task.title}",
        "",
        pack.task.statement.strip(),
        "",
        f"- 检索词：`{pack.query}`",
        f"- 预算：{pack.budget} tokens，实际 {pack.tokens} tokens"
        + ("（已截断）" if pack.truncated else ""),
        f"- 候选：{pack.db_rows} 条（来自 seekdb 全文检索 + 精确来源匹配）",
        "",
        "## 一、适用规格",
        "",
    ]
    for hit in pack.specs:
        lines.append(_block(f"{hit.symbol}", hit.source, hit.text))
    lines += ["## 二、需要修改的代码", ""]
    for hit in pack.code:
        lines.append(_block(f"{hit.symbol}", hit.source, hit.text))
    lines += ["## 三、验证用测试", ""]
    for hit in pack.tests:
        lines.append(_block(f"{hit.symbol}", hit.source, hit.text))
    lines += ["## 四、历史任务状态（PowerContext）", ""]
    if pack.state:
        groups = {"run": [], "validation": [], "todo": []}
        for record in pack.state:
            groups.setdefault(str(record.get("kind", "run")), []).append(record)
        for kind, label in (("run", "已完成"), ("validation", "已有验证结果"), ("todo", "待办")):
            for record in groups.get(kind, []):
                suffix = ""
                if record.get("items"):
                    suffix = f"；待办项：{', '.join(str(item) for item in record['items'])}"
                lines.append(
                    f"- [{label}] `{record.get('run_id', '?')}` "
                    f"{record.get('status', '?')}（{record.get('saved_at', '?')}）："
                    f"{record.get('summary', '')}{suffix}"
                )
    else:
        lines.append("- 无历史记录（本次从空状态开始）")
    if pack.notes:
        lines += ["", "## 备注", ""] + [f"- {note}" for note in pack.notes]
    return "\n".join(lines).rstrip() + "\n"


_SYSTEM_PROMPT = """你是资深 Python 工程师，只输出一个可被 `git apply -p1` 直接应用的 unified diff。
硬性要求：
1. 只修改生产代码，绝不修改 tests/ 下的任何文件，也不要新增测试。
2. 严格遵守给出的规格条目；不确定时保持现有公开 API 不变。
3. diff 必须包含 `--- a/<path>`、`+++ b/<path>` 与 `@@` hunk 头，路径相对工程根目录。
4. 只输出 diff；可以用 ```diff 围栏包裹，围栏外不要写任何解释。
5. 所有金额使用 decimal.Decimal，禁止 float；金额舍入遵循规格。"""


def build_prompt(pack: ContextPack, *, test_command: str = "python -m pytest -q") -> list[dict[str, str]]:
    """Chat messages for MiniMax. Context first, then the task and output contract."""
    spec_text = "\n\n".join(
        f"[{hit.symbol}] {hit.source}\n{hit.text}" for hit in pack.specs
    )
    code_text = "\n\n".join(
        f"FILE {hit.path} ({hit.start_line}-{hit.end_line})\n{hit.text}" for hit in pack.code
    )
    test_text = "\n\n".join(f"[{hit.symbol}] {hit.source}\n{hit.text}" for hit in pack.tests)
    state_text = (
        "\n".join(
            f"- [{record.get('kind', 'run')}] {record.get('run_id', '?')} "
            f"{record.get('status', '?')}：{record.get('summary', '')}"
            for record in pack.state
        )
        or "（无历史记录）"
    )
    user = f"""## 任务
{pack.task.id}：{pack.task.title}

{pack.task.statement}

验收条件：
{chr(10).join(f"- {item}" for item in pack.task.acceptance) or "- 目标测试全部通过"}

## 适用规格（必须逐条满足）
{spec_text}

## 现有代码
{code_text}

## 验证测试（不得修改）
{test_text}

## 历史任务状态（来自 PowerContext）
{state_text}

## 输出
只输出 unified diff，路径相对工程根目录。修改后用 `{test_command}` 应全部通过。"""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def pack_json(pack: ContextPack) -> str:
    return json.dumps(pack.to_dict(), ensure_ascii=False, indent=2)
