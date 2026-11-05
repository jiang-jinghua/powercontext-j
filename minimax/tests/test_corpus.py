from __future__ import annotations

from pathlib import Path

import pytest

from sddctx.corpus import load_project

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "demo" / "order-service"


@pytest.fixture(scope="module")
def corpus():
    return load_project(PROJECT, "order-service")


def test_specs_are_split_into_clauses(corpus) -> None:
    specs = [chunk for chunk in corpus.chunks if chunk.kind == "spec"]
    symbols = {chunk.symbol for chunk in specs}
    assert {"SPEC-PRC-001", "SPEC-PRC-002", "SPEC-PRM-002", "SPEC-SHP-001"} <= symbols
    assert len(specs) == 12


def test_spec_provenance_points_at_the_heading(corpus) -> None:
    chunk = next(chunk for chunk in corpus.chunks if chunk.symbol == "SPEC-PRC-002")
    lines = (PROJECT / chunk.path).read_text(encoding="utf-8").splitlines()
    assert lines[chunk.start_line - 1].startswith("## SPEC-PRC-002")
    assert chunk.text.startswith("## SPEC-PRC-002")


def test_code_symbols_use_package_names(corpus) -> None:
    names = {chunk.symbol for chunk in corpus.chunks if chunk.kind == "code"}
    assert "order_service.promotion.apply_coupon" in names
    assert "order_service.service.quote" in names


def test_tests_are_indexed_by_node_id(corpus) -> None:
    names = {chunk.symbol for chunk in corpus.chunks if chunk.kind == "test"}
    assert "tests/test_coupon.py::test_save30_applies_at_threshold" in names


def test_overlay_replaces_one_spec_and_bumps_version() -> None:
    overlay = ROOT / "demo" / "spec-updates" / "2026.2" / "promotion.md"
    updated = load_project(PROJECT, "order-service", spec_version="2026.2", spec_overlay=overlay)
    assert updated.spec_version == "2026.2"
    clause = next(chunk for chunk in updated.chunks if chunk.symbol == "SPEC-PRM-002")
    assert "150" in clause.text
    assert "200 元" not in clause.text
