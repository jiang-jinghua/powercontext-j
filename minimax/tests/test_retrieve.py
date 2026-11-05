from __future__ import annotations

from sddctx.retrieve import Task, retrieve
from sddctx.store import Hit


def make_hit(
    chunk_id: str,
    kind: str,
    symbol: str,
    text: str,
    *,
    path: str | None = None,
    score: float = 1.0,
    tokens: int = 40,
) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        doc_id="doc",
        kind=kind,
        symbol=symbol,
        path=path or f"src/{symbol}.py",
        start_line=1,
        end_line=2,
        tokens=tokens,
        text=text,
        score=score,
    )


class FakeStore:
    def __init__(self, fts: list[Hit], exact: list[Hit]) -> None:
        self.fts = fts
        self.exact_hits = exact
        self.search_calls: list[str] = []

    def search(self, project: str, query: str, limit: int = 40) -> list[Hit]:
        self.search_calls.append(query)
        return self.fts[:limit]

    def exact(self, project: str, *, symbols=(), paths=()) -> list[Hit]:
        return self.exact_hits


def task(**overrides) -> Task:
    data = {
        "id": "T-001",
        "title": "SAVE30",
        "project": "order-service",
        "statement": "实现 SAVE30 并修正舍入",
        "spec_refs": ("SPEC-PRM-002",),
        "symbols": ("order_service.promotion.apply_coupon",),
        "paths": ("src/order_service/promotion.py",),
        "target_tests": ("tests/test_coupon.py::test_save30_applies_at_threshold",),
        "acceptance": (),
    }
    data.update(overrides)
    return Task(**data)


def test_required_sources_are_kept_even_when_fts_misses_them() -> None:
    spec = make_hit("c-spec", "spec", "SPEC-PRM-002", "满200减30")
    code = make_hit("c-code", "code", "order_service.promotion.apply_coupon", "def apply_coupon")
    test = make_hit("c-test", "test", "tests/test_coupon.py::test_save30_applies_at_threshold", "def test")
    store = FakeStore(fts=[], exact=[spec, code, test])
    pack = retrieve(store, task(), budget=2000)
    assert [hit.symbol for hit in pack.specs] == ["SPEC-PRM-002"]
    assert [hit.symbol for hit in pack.code] == ["order_service.promotion.apply_coupon"]
    assert [hit.symbol for hit in pack.tests] == [
        "tests/test_coupon.py::test_save30_applies_at_threshold"
    ]
    assert pack.notes == []


def test_search_query_is_built_from_the_task() -> None:
    store = FakeStore([], [])
    pack = retrieve(store, task(), budget=1000)
    assert store.search_calls and "SAVE30" in store.search_calls[0]
    assert pack.query


def test_tiny_budget_truncates_and_says_so() -> None:
    hits = [make_hit(f"c{index}", "code", f"mod.fn{index}", "x" * 400, tokens=200) for index in range(5)]
    store = FakeStore(fts=hits, exact=[])
    pack = retrieve(store, task(spec_refs=(), symbols=(), target_tests=()), budget=250)
    assert pack.truncated is True
    assert pack.tokens <= 260


def test_pack_dict_exposes_provenance() -> None:
    spec = make_hit("c-spec", "spec", "SPEC-PRM-002", "满200减30", path="specs/promotion.md")
    pack = retrieve(FakeStore([spec], [spec]), task(), budget=2000)
    data = pack.to_dict()
    assert data["spec"][0]["source"] == "specs/promotion.md:1-2"
    assert data["task"]["id"] == "T-001"
