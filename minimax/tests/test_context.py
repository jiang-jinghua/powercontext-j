from __future__ import annotations

import json

from sddctx.context import build_prompt, pack_json, render_markdown
from sddctx.retrieve import ContextPack, Task
from sddctx.store import Hit


def pack() -> ContextPack:
    task = Task(
        id="T-001",
        title="SAVE30",
        project="order-service",
        statement="实现 SAVE30",
        spec_refs=("SPEC-PRM-002",),
        symbols=("order_service.promotion.apply_coupon",),
        target_tests=("tests/test_coupon.py::test_save30_applies_at_threshold",),
        acceptance=("目标测试通过",),
    )
    spec = Hit("c1", "d1", "spec", "SPEC-PRM-002", "specs/promotion.md", 12, 15, 30, "满 200 减 30")
    code = Hit("c2", "d2", "code", "order_service.promotion.apply_coupon", "src/order_service/promotion.py", 27, 30, 30, "def apply_coupon")
    test = Hit("c3", "d3", "test", "tests/test_coupon.py::test_save30_applies_at_threshold", "tests/test_coupon.py", 17, 20, 30, "def test")
    return ContextPack(task=task, specs=[spec], code=[code], tests=[test], query="SAVE30", db_rows=3, tokens=90, budget=3600)


def test_markdown_cites_every_source() -> None:
    rendered = render_markdown(pack())
    assert "specs/promotion.md:12-15" in rendered
    assert "src/order_service/promotion.py:27-30" in rendered
    assert "tests/test_coupon.py:17-20" in rendered


def test_prompt_carries_specs_and_output_contract() -> None:
    messages = build_prompt(pack())
    system, user = messages[0]["content"], messages[1]["content"]
    assert "unified diff" in system
    assert "满 200 减 30" in user
    assert "不得修改" in system or "不得修改" in user


def test_pack_json_is_valid_json() -> None:
    data = json.loads(pack_json(pack()))
    assert data["spec"][0]["symbol"] == "SPEC-PRM-002"
    assert data["budget_tokens"] == 3600
