from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sddctx.errors import PatchError
from sddctx.patch import apply_patch, extract_diff, touched_paths

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "demo" / "order-service"
PATCH = ROOT / "patches" / "T-001.patch"


def copy_project(tmp_path: Path) -> Path:
    target = tmp_path / "order-service"
    shutil.copytree(PROJECT, target)
    return target


def test_reference_patch_changes_two_files(tmp_path: Path) -> None:
    workspace = copy_project(tmp_path)
    changes = apply_patch(PATCH.read_text(encoding="utf-8"), workspace)
    assert {change.path for change in changes} == {
        "src/order_service/money.py",
        "src/order_service/promotion.py",
    }
    assert "ROUND_HALF_UP" in (workspace / "src/order_service/money.py").read_text(encoding="utf-8")


def test_reference_patch_makes_the_suite_green(tmp_path: Path) -> None:
    workspace = copy_project(tmp_path)
    apply_patch(PATCH.read_text(encoding="utf-8"), workspace)
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout[-2000:]


def test_patch_may_not_touch_tests(tmp_path: Path) -> None:
    workspace = copy_project(tmp_path)
    diff = """--- a/tests/test_money.py
+++ b/tests/test_money.py
@@ -1,3 +1,3 @@
 from decimal import Decimal
-from order_service.money import money, round_money
+from order_service.money import money
"""
    with pytest.raises(PatchError):
        apply_patch(diff, workspace)


def test_diff_is_extracted_from_a_fenced_reply() -> None:
    reply = "here you go\n```diff\n" + PATCH.read_text(encoding="utf-8") + "```\nthanks"
    assert touched_paths(extract_diff(reply)) == [
        "src/order_service/money.py",
        "src/order_service/promotion.py",
    ]


def test_missing_diff_is_rejected() -> None:
    with pytest.raises(PatchError):
        extract_diff("I changed the code, trust me.")
