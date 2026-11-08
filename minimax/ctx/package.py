"""上下文包：把检索结果整理成"规格—代码—测试—状态"四段式材料。

该包既是喂给 MiniMax 的提示词主体，也是赛题 2.3 要求"输出必须包含"的物化产物：
- 本次任务使用的规格条目（编码 + 标题 + 来源文件 + 版本）
- 本次修改涉及的代码文件与函数
- 用于验证功能与兼容性的测试
- 已完成 / 待办 / 已知遗留（来自状态存储）
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from .store import Hit

MAX_EXCERPT = 1400
MAX_FILE_CHARS = 20000      # 单个目标文件上限
MAX_TOTAL_FILE_CHARS = 80000  # 全部目标文件合计上限


@dataclass
class SpecItem:
    code: str
    title: str
    path: str
    version: str
    chunk_id: str
    excerpt: str


@dataclass
class CodeRef:
    path: str
    symbol: str
    chunk_id: str
    score: float
    excerpt: str


@dataclass
class TestRef:
    path: str
    symbol: str
    chunk_id: str
    score: float
    excerpt: str


@dataclass
class ContextPackage:
    task_id: str
    task_title: str
    spec_version: str
    spec_items: list[SpecItem] = field(default_factory=list)
    code_refs: list[CodeRef] = field(default_factory=list)
    test_refs: list[TestRef] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    )

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, task: dict, retrieval, state: dict | None = None) -> "ContextPackage":
        spec_items: list[SpecItem] = []
        for hit in retrieval.spec_hits:
            code = next(
                (c for c in hit.chunk.spec_codes if c.startswith("SPEC-")),
                hit.chunk.spec_codes[0] if hit.chunk.spec_codes else "UNKNOWN",
            )
            spec_items.append(
                SpecItem(
                    code=code,
                    title=hit.chunk.title,
                    path=hit.chunk.path,
                    version=hit.chunk.version,
                    chunk_id=hit.chunk.chunk_id,
                    excerpt=_excerpt(hit.chunk.text),
                )
            )
        code_refs = [
            CodeRef(
                path=h.chunk.path,
                symbol=h.chunk.symbol,
                chunk_id=h.chunk.chunk_id,
                score=round(h.score, 4),
                excerpt=_excerpt(h.chunk.text),
            )
            for h in retrieval.code_hits
        ]
        test_refs = [
            TestRef(
                path=h.chunk.path,
                symbol=h.chunk.symbol,
                chunk_id=h.chunk.chunk_id,
                score=round(h.score, 4),
                excerpt=_excerpt(h.chunk.text),
            )
            for h in retrieval.test_hits
        ]
        versions = {i.version for i in spec_items if i.version}
        return cls(
            task_id=task["task_id"],
            task_title=task.get("title", ""),
            spec_version=",".join(sorted(versions)) if versions else "",
            spec_items=spec_items,
            code_refs=code_refs,
            test_refs=test_refs,
            state=state or {},
            files=_load_target_files(task.get("target_files", [])),
            evidence=retrieval.evidence_ids(),
            queries=retrieval.queries,
            missing=list(retrieval.missing),
        )

    # ------------------------------------------------------------------ output
    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_title": self.task_title,
            "spec_version": self.spec_version,
            "generated_at": self.generated_at,
            "spec_items": [i.__dict__ for i in self.spec_items],
            "code_refs": [c.__dict__ for c in self.code_refs],
            "test_refs": [t.__dict__ for t in self.test_refs],
            "state": self.state,
            "files": self.files,
            "evidence": self.evidence,
            "queries": self.queries,
            "missing": self.missing,
        }

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# 任务上下文包 {self.task_id} · {self.task_title}")
        lines.append("")
        lines.append(f"- 生成时间：{self.generated_at}")
        lines.append(f"- 规格版本：{self.spec_version or 'unknown'}")
        lines.append(f"- 召回证据：{len(self.evidence)} 个片段")
        if self.missing:
            lines.append(f"- 缺失规格条目：{', '.join(self.missing)}")
        lines.append("")
        if self.files:
            lines.append("## 0. 目标文件全文")
            lines.append("")
            lines.append("> 生成补丁必须基于下面这些真实内容构造 hunk，不要臆测行号或省略上下文。")
            lines.append("")
            for f in self.files:
                lines.append(f"### `{f['path']}`（{f['bytes']} 字节）")
                lines.append("")
                lines.append("```python")
                lines.append(f["text"])
                lines.append("```")
                lines.append("")
        lines.append("## 1. 规格条目")
        lines.append("")
        for item in self.spec_items:
            lines.append(f"### {item.code} · {item.title}")
            lines.append(f"- 来源：`{item.path}`（版本 {item.version or 'n/a'}）")
            lines.append(f"- 片段：`{item.chunk_id}`")
            lines.append("")
            lines.append("```text")
            lines.append(item.excerpt)
            lines.append("```")
            lines.append("")
        lines.append("## 2. 相关代码")
        lines.append("")
        for ref in self.code_refs:
            lines.append(f"### `{ref.path}` :: `{ref.symbol}`")
            lines.append(f"- 片段：`{ref.chunk_id}`（score={ref.score}）")
            lines.append("")
            lines.append("```python")
            lines.append(ref.excerpt)
            lines.append("```")
            lines.append("")
        lines.append("## 3. 验证测试")
        lines.append("")
        for ref in self.test_refs:
            lines.append(f"### `{ref.path}` :: `{ref.symbol}`")
            lines.append(f"- 片段：`{ref.chunk_id}`（score={ref.score}）")
            lines.append("")
            lines.append("```python")
            lines.append(ref.excerpt)
            lines.append("```")
            lines.append("")
        lines.append("## 4. 任务状态")
        lines.append("")
        state = self.state or {}
        lines.append(f"- 已完成：{'; '.join(state.get('done', [])) or '无'}")
        lines.append(f"- 待办：{'; '.join(state.get('todo', [])) or '无'}")
        lines.append(f"- 已知遗留：{'; '.join(state.get('risks', [])) or '无'}")
        if state.get("parent_handoff"):
            lines.append("")
            lines.append("### 上游任务交接（PowerContext 恢复）")
            lines.append("")
            lines.append("```text")
            lines.append(state["parent_handoff"].strip())
            lines.append("```")
        lines.append("")
        lines.append("## 5. 检索留痕")
        lines.append("")
        for q in self.queries:
            top = ", ".join(h["chunk_id"] for h in q["hits"][:3]) or "无命中"
            lines.append(f"- [{q['route']}] `{q['query']}` → {top}")
        lines.append("")
        return "\n".join(lines)

    def save(self, directory: str | Path) -> dict[str, str]:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        md = out / "context.md"
        js = out / "context.json"
        md.write_text(self.to_markdown(), encoding="utf-8")
        js.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"markdown": str(md), "json": str(js)}


def _load_target_files(paths: list[str]) -> list[dict]:
    """读取任务的目标文件全文，供模型生成可直接应用的 hunk。

    只读取仓库内的相对路径；单文件与合计都有上限，避免提示词无限膨胀。
    """
    root = Path(__file__).resolve().parent.parent
    files: list[dict] = []
    budget = MAX_TOTAL_FILE_CHARS
    for rel in paths:
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        if budget <= 0:
            break
        limit = min(MAX_FILE_CHARS, budget)
        text = raw if len(raw) <= limit else raw[:limit] + "\n...（已截断）"
        files.append(
            {
                "path": rel,
                "bytes": len(raw.encode("utf-8")),
                "truncated": len(raw) > limit,
                "text": text,
            }
        )
        budget -= len(text)
    return files


def _excerpt(text: str, limit: int = MAX_EXCERPT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...（已截断）"
