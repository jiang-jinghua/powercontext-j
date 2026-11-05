"""Task model and the retrieval that turns a task into a cited context pack."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .corpus import estimate_tokens
from .errors import RetrievalError
from .store import CorpusStore, Hit

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}")


@dataclass(frozen=True)
class Task:
    """One local change request."""

    id: str
    title: str
    project: str
    statement: str
    spec_refs: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    target_tests: tuple[str, ...] = ()
    regression_tests: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path) -> Task:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        required = ("id", "title", "project", "statement")
        missing = [key for key in required if not data.get(key)]
        if missing:
            raise RetrievalError(f"{path.name} is missing fields: {', '.join(missing)}")
        return cls(
            id=str(data["id"]),
            title=str(data["title"]),
            project=str(data["project"]),
            statement=str(data["statement"]).strip(),
            spec_refs=tuple(data.get("spec_refs") or ()),
            symbols=tuple(data.get("symbols") or ()),
            paths=tuple(data.get("paths") or ()),
            target_tests=tuple(data.get("target_tests") or ()),
            regression_tests=tuple(data.get("regression_tests") or ()),
            acceptance=tuple(data.get("acceptance") or ()),
        )

    def query_terms(self) -> list[str]:
        """Terms that should match the corpus, deduplicated in first-seen order."""
        text = " ".join((self.title, self.statement, " ".join(self.acceptance)))
        terms = _TOKEN_RE.findall(text)
        for symbol in self.symbols:
            terms.extend(symbol.split(".")[-1].split("::")[-1:])
        for path in self.paths:
            terms.append(Path(path).stem)
        for test in self.target_tests:
            terms.extend(test.split("::")[-1].split("_"))
        seen: set[str] = set()
        ordered: list[str] = []
        for term in terms:
            lowered = term.lower()
            if lowered in seen or len(lowered) < 2:
                continue
            seen.add(lowered)
            ordered.append(term)
        return ordered[:48]

    def fts_query(self) -> str:
        return " ".join(self.query_terms())


@dataclass
class ContextPack:
    """The cited materials a developer/agent needs for one task."""

    task: Task
    specs: list[Hit] = field(default_factory=list)
    code: list[Hit] = field(default_factory=list)
    tests: list[Hit] = field(default_factory=list)
    state: list[dict[str, object]] = field(default_factory=list)
    query: str = ""
    db_rows: int = 0
    tokens: int = 0
    budget: int = 0
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def all_hits(self) -> list[Hit]:
        return [*self.specs, *self.code, *self.tests]

    def to_dict(self) -> dict[str, object]:
        return {
            "task": {
                "id": self.task.id,
                "title": self.task.title,
                "project": self.task.project,
            },
            "query": self.query,
            "budget_tokens": self.budget,
            "tokens": self.tokens,
            "truncated": self.truncated,
            "db_rows": self.db_rows,
            "spec": [self._hit_dict(hit) for hit in self.specs],
            "code": [self._hit_dict(hit) for hit in self.code],
            "tests": [self._hit_dict(hit) for hit in self.tests],
            "state": self.state,
            "notes": self.notes,
        }

    @staticmethod
    def _hit_dict(hit: Hit) -> dict[str, object]:
        return {
            "symbol": hit.symbol,
            "source": hit.source,
            "path": hit.path,
            "kind": hit.kind,
            "score": round(hit.score, 4),
            "text": hit.text,
        }


def _normalised_scores(hits: list[Hit]) -> dict[str, float]:
    if not hits:
        return {}
    best = max(hit.score for hit in hits) or 1.0
    return {hit.chunk_id: hit.score / best for hit in hits}


def retrieve(
    store: CorpusStore,
    task: Task,
    *,
    state: list[dict[str, object]] | None = None,
    budget: int = 3600,
) -> ContextPack:
    """Query seekdb and select a budgeted, provenance-preserving context pack."""
    query = task.fts_query()
    if not query:
        raise RetrievalError("task produced an empty search query")

    fts_hits = store.search(task.project, query, limit=80)
    exact_hits = store.exact(
        task.project,
        symbols=tuple(dict.fromkeys((*task.spec_refs, *task.symbols))),
        paths=task.paths,
    )
    all_hits: dict[str, Hit] = {}
    for hit in (*exact_hits, *fts_hits):
        all_hits.setdefault(hit.chunk_id, hit)

    normalised = _normalised_scores(fts_hits)
    ranked: list[Hit] = []
    for hit in all_hits.values():
        score = normalised.get(hit.chunk_id, 0.0)
        if hit.symbol in task.spec_refs:
            score += 3.0
        if hit.symbol in task.symbols:
            score += 2.5
        if hit.path in task.paths:
            score += 2.0
        if any(hit.symbol.endswith(test.split("::")[-1]) for test in task.target_tests):
            score += 1.5
        if hit.kind == "test" and any(
            symbol.split(".")[-1].split("::")[-1] in hit.text for symbol in task.symbols
        ):
            score += 0.4
        ranked.append(
            Hit(
                chunk_id=hit.chunk_id,
                doc_id=hit.doc_id,
                kind=hit.kind,
                symbol=hit.symbol,
                path=hit.path,
                start_line=hit.start_line,
                end_line=hit.end_line,
                tokens=hit.tokens,
                text=hit.text,
                score=score,
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.kind, item.path, item.start_line))

    pack = ContextPack(task=task, query=query, budget=budget, db_rows=len(ranked), state=list(state or ()))

    required_specs = [hit for hit in ranked if hit.symbol in task.spec_refs]
    required_code = [hit for hit in ranked if hit.symbol in task.symbols]
    required_tests = [
        hit for hit in ranked if any(hit.symbol.endswith(test.split("::")[-1]) for test in task.target_tests)
    ]

    chosen: set[str] = set()

    def admit(hit: Hit) -> bool:
        if hit.chunk_id in chosen:
            return False
        if pack.tokens + hit.tokens > budget and chosen:
            pack.truncated = True
            return False
        chosen.add(hit.chunk_id)
        pack.tokens += hit.tokens
        return True

    for hit in (*required_specs, *required_code, *required_tests):
        admit(hit)

    def admit_bucket(kind: str, minimum: int) -> None:
        current = sum(1 for hit in ranked if hit.chunk_id in chosen and hit.kind == kind)
        for hit in ranked:
            if current >= minimum:
                break
            if hit.kind == kind and admit(hit):
                current += 1

    for kind, minimum in (("spec", 2), ("code", 2), ("test", 2)):
        admit_bucket(kind, minimum)

    for hit in ranked:
        if len(chosen) >= 12:
            break
        admit(hit)

    for hit in ranked:
        if hit.chunk_id not in chosen:
            continue
        if hit.kind == "spec":
            pack.specs.append(hit)
        elif hit.kind == "test":
            pack.tests.append(hit)
        else:
            pack.code.append(hit)

    for name, bucket in (("spec", pack.specs), ("code", pack.code), ("tests", pack.tests)):
        if not bucket:
            pack.notes.append(f"no {name} chunk matched; check the imported corpus")
    return pack
