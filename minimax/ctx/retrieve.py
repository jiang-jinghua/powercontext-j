"""面向任务的多路召回。

一个"任务"需要三类上下文，缺一不可（对应赛题 2.3 的硬性要求）：
1. 规格条目：任务卡声明了 ``spec_codes``，逐条检索并定位到具体小节；
2. 代码位置：任务卡声明了 ``target_files``，按文件 + 语义双路召回函数级片段；
3. 验证测试：按规格编码召回验收用例（用例 docstring 里带 SPEC 编码）。

召回同时保留完整 hit 列表，写入 run 留痕，便于评审复核"检索结果确实用于上下文"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .store import BaseStore, Hit


@dataclass
class Retrieval:
    queries: list[dict] = field(default_factory=list)
    spec_hits: list[Hit] = field(default_factory=list)
    code_hits: list[Hit] = field(default_factory=list)
    test_hits: list[Hit] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def all_hits(self) -> list[Hit]:
        seen: dict[str, Hit] = {}
        for hit in [*self.spec_hits, *self.code_hits, *self.test_hits]:
            if hit.chunk.chunk_id not in seen or hit.score > seen[hit.chunk.chunk_id].score:
                seen[hit.chunk.chunk_id] = hit
        return sorted(seen.values(), key=lambda h: h.score, reverse=True)

    def evidence_ids(self) -> list[str]:
        return [h.chunk.chunk_id for h in self.all_hits()]


def _dedupe(hits: Sequence[Hit]) -> list[Hit]:
    seen: dict[str, Hit] = {}
    for h in hits:
        key = h.chunk.chunk_id
        if key not in seen or h.score > seen[key].score:
            seen[key] = h
    return sorted(seen.values(), key=lambda h: h.score, reverse=True)


def _spec_chunk_for_code(hits: Sequence[Hit], code: str) -> Hit | None:
    best: Hit | None = None
    for h in hits:
        if code not in h.chunk.spec_codes:
            continue
        # 标题即该条目的小节更精准
        if code in h.chunk.title:
            return h
        if best is None or h.score > best.score:
            best = h
    return best


def retrieve_for_task(
    store: BaseStore,
    task: dict,
    *,
    k_spec: int = 8,
    k_code: int = 10,
    k_test: int = 8,
) -> Retrieval:
    title = task.get("title", "")
    request = task.get("request", "")
    spec_codes: list[str] = list(task.get("spec_codes", []))
    target_files: list[str] = list(task.get("target_files", []))
    result = Retrieval()

    def record(route: str, query: str, hits: list[Hit]) -> None:
        result.queries.append(
            {
                "route": route,
                "query": query,
                "hits": [{"chunk_id": h.chunk.chunk_id, "path": h.chunk.path, "score": round(h.score, 4)} for h in hits],
            }
        )

    # 1) 规格条目：语义召回 + 编码精确召回
    spec_pool: list[Hit] = []
    for query in (f"{title} {request}", *spec_codes):
        hits = store.search(query, k=k_spec, kinds=["spec"])
        record("spec", query, hits)
        spec_pool.extend(hits)
    spec_pool = _dedupe(spec_pool)
    for code in spec_codes:
        hit = _spec_chunk_for_code(spec_pool, code)
        if hit is None:
            hit = _spec_chunk_for_code(store.search(code, k=k_spec, kinds=["spec"]), code)
        if hit is None:
            result.missing.append(code)
        else:
            result.spec_hits.append(hit)

    # 2) 代码位置：按目标文件限定 + 语义召回
    code_pool: list[Hit] = []
    for path in target_files:
        stem = Path(path).stem
        for query in (f"{stem} {title}", f"{path} {request}"):
            hits = store.search(query, k=k_code, kinds=["code"])
            record("code", query, hits)
            code_pool.extend([h for h in hits if h.chunk.path == path] or hits)
    if not target_files:
        hits = store.search(f"{title} {request}", k=k_code, kinds=["code"])
        record("code", f"{title} {request}", hits)
        code_pool.extend(hits)
    result.code_hits = _dedupe(code_pool)[: k_code * 2]

    # 3) 验证测试：按规格编码召回
    test_pool: list[Hit] = []
    for query in (f"{' '.join(spec_codes)} {title}", request):
        hits = store.search(query, k=k_test, kinds=["test"])
        record("test", query, hits)
        test_pool.extend(hits)
    result.test_hits = _dedupe(test_pool)[: k_test * 2]

    return result
