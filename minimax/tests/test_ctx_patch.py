"""补丁与模型输出处理的单元测试（不需要网络与外部服务）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ctx import minimax, patch  # noqa: E402


def test_repair_recounts_hunk_header_and_cleans_markers():
    diff = """```diff
diff --git a/src/x.py b/src/x.py
--- a/src/x.py
+++ b/src/x.py
@@ -10,5 +10,8 @@
 ctx
+added one
+added two

 ctx2
*** End Patch
"""
    fixed, notes = patch.repair_diff(diff)
    assert "*** End Patch" not in fixed
    assert "```" not in fixed
    assert "@@ -10,3 +10,5 @@" in fixed, fixed
    assert any("重算 hunk 行数" in n for n in notes)


def test_repair_turns_blank_line_into_context():
    diff = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n\n-x\n+y\n"
    fixed, _ = patch.repair_diff(diff)
    body = fixed.splitlines()[4]
    assert body == " ", repr(body)


def test_strip_reasoning_keeps_only_final_answer():
    raw = "<think>草稿</think>\n最终答案"
    assert minimax.strip_reasoning(raw) == "最终答案"


def test_extract_files_parses_blocks():
    text = "说明\n```file path=src/settlement/a.py\nprint(1)\n```\n```file path=tests/x.py\npass\n```"
    files = minimax.extract_files(text)
    assert files["src/settlement/a.py"] == "print(1)"
    assert files["tests/x.py"] == "pass"


def test_write_files_refuses_tests_and_outside_paths(tmp_path):
    written, rejected = patch.write_files(
        tmp_path,
        {
            "src/ok.py": "print(1)",
            "tests/bad.py": "print(2)",
            "../escape.py": "print(3)",
        },
    )
    assert written == ["src/ok.py"]
    assert set(rejected) == {"tests/bad.py", "../escape.py"}
    assert (tmp_path / "src/ok.py").read_text(encoding="utf-8") == "print(1)\n"
    assert not (tmp_path / "tests/bad.py").exists()
