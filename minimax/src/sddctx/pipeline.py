"""End-to-end SDD run: retrieve context, ask MiniMax, apply diff, verify tests, save state."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .context import build_prompt, pack_json, render_markdown
from .errors import PatchError
from .minimax import Completion, Provider
from .patch import FileChange, apply_patch, extract_diff, touched_paths
from .retrieve import ContextPack, Task, retrieve
from .state import StateStore
from .store import CorpusStore

_OUTCOME_RE = re.compile(r"^(?P<node>[\w./\\-]+\.py::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)")
_COUNT_RE = re.compile(r"(\d+) passed")


@dataclass
class TestRun:
    """One pytest invocation in a workspace copy."""

    exit_code: int
    outcomes: dict[str, str] = field(default_factory=dict)
    output: str = ""
    duration_ms: int = 0

    @property
    def failed(self) -> set[str]:
        return {name for name, outcome in self.outcomes.items() if outcome in {"FAILED", "ERROR"}}

    @property
    def passed(self) -> set[str]:
        return {name for name, outcome in self.outcomes.items() if outcome == "PASSED"}

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "passed": len(self.passed),
            "failed": sorted(self.failed),
            "outcomes": dict(sorted(self.outcomes.items())),
        }


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    status: str
    verification: dict[str, object]
    context: ContextPack
    completion: Completion
    changes: list[FileChange]
    before: TestRun
    after: TestRun

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "verification": self.verification,
            "provider": self.completion.provider,
            "model": self.completion.model,
            "context": {
                "spec": [hit.symbol for hit in self.context.specs],
                "code": [hit.symbol for hit in self.context.code],
                "tests": [hit.symbol for hit in self.context.tests],
                "tokens": self.context.tokens,
                "db_rows": self.context.db_rows,
            },
            "changes": [change.__dict__ for change in self.changes],
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }


def run_pytest(workspace: Path, *, timeout: int = 300) -> TestRun:
    """Run the project's test suite with a clean environment."""
    import os

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    env.pop("PYTHONPATH", None)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short", "-p", "no:cacheprovider"],
            cwd=workspace,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        output = completed.stdout + completed.stderr
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = f"pytest timed out after {timeout}s\n{exc.stdout or ''}"
        exit_code = 124
    outcomes: dict[str, str] = {}
    for line in output.splitlines():
        match = _OUTCOME_RE.match(line.strip())
        if match:
            outcomes[match.group("node").replace("\\", "/")] = match.group("outcome")
    return TestRun(
        exit_code=exit_code,
        outcomes=outcomes,
        output=output,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


def _copy_project(project_dir: Path, workspace: Path) -> None:
    shutil.copytree(
        project_dir,
        workspace,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".git"),
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tree_hash(root: Path) -> str:
    """Hash every file below ``root`` so a test suite can be proven untouched."""
    digest = hashlib.sha256()
    if not root.is_dir():
        return digest.hexdigest()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _state_brief(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Reduce the journal to the fields the prompt needs."""
    brief: list[dict[str, object]] = []
    for record in records[-8:]:
        item: dict[str, object] = {
            "kind": record.get("kind", "record"),
            "run_id": record.get("run_id", ""),
            "task_id": record.get("task_id", ""),
            "status": record.get("status", ""),
            "summary": record.get("summary", ""),
            "saved_at": record.get("saved_at", ""),
        }
        if record.get("items"):
            item["items"] = record["items"]
        checks = record.get("checks")
        if isinstance(checks, dict):
            item["checks"] = {
                key: checks[key]
                for key in ("target_failed_before", "target_passed_after", "no_new_failures")
                if key in checks
            }
        brief.append(item)
    return brief


def run_task(
    settings: Settings,
    *,
    task_id: str,
    provider: Provider,
    store: CorpusStore,
    tasks_dir: Path,
    budget: int = 3600,
) -> RunResult:
    """Execute one task end to end and persist its artifacts and state."""
    task = Task.load(tasks_dir / f"{task_id}.yaml")
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{task.id}"
    run_dir = settings.runs_dir / run_id
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=True)
    _copy_project(settings.project_dir, workspace)
    tests_hash = _tree_hash(workspace / "tests")

    before = run_pytest(workspace)
    (run_dir / "pytest-before.log").write_text(before.output, encoding="utf-8")

    state_store = StateStore(settings.state_db, settings.scheduler_db)
    records = state_store.recall(task.project)
    pack = retrieve(store, task, state=_state_brief(records), budget=budget)
    (run_dir / "context.json").write_text(pack_json(pack), encoding="utf-8")
    (run_dir / "context.md").write_text(render_markdown(pack), encoding="utf-8")

    messages = build_prompt(pack)
    (run_dir / "prompt.md").write_text(
        "\n\n".join(f"## {message['role']}\n\n{message['content']}" for message in messages),
        encoding="utf-8",
    )
    completion = provider.complete(messages, record_dir=run_dir / "model")
    (run_dir / "model-reply.md").write_text(completion.content, encoding="utf-8")

    diff_text = extract_diff(completion.content)
    (run_dir / "diff.patch").write_text(diff_text, encoding="utf-8")
    paths = touched_paths(diff_text)
    for relative in paths:
        source = workspace / relative
        if source.is_file():
            snapshot = run_dir / "snapshots" / "before" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, snapshot)

    changes = apply_patch(diff_text, workspace)
    for change in changes:
        snapshot = run_dir / "snapshots" / "after" / change.path
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / change.path, snapshot)

    after = run_pytest(workspace)
    (run_dir / "pytest-after.log").write_text(after.output, encoding="utf-8")

    target = set(task.target_tests)
    after_tests_hash = _tree_hash(workspace / "tests")
    verification = {
        "target_tests": sorted(target),
        "target_failed_before": sorted(target & before.failed),
        "target_passed_after": sorted(target & after.passed),
        "regression_passed": sorted(set(after.passed) - target),
        "no_new_failures": not after.failed,
        "tests_untouched": (
            all(not path.startswith("tests/") for path in paths) and tests_hash == after_tests_hash
        ),
        "tests_tree_sha256_before": tests_hash,
        "tests_tree_sha256_after": after_tests_hash,
        "spec_refs_in_context": sorted(set(task.spec_refs) - {hit.symbol for hit in pack.specs}),
        "target_tests_in_context": sorted(target - {hit.symbol for hit in pack.tests}),
    }
    ok = (
        target
        and target <= before.failed
        and target <= after.passed
        and not after.failed
        and not verification["spec_refs_in_context"]
        and not verification["target_tests_in_context"]
    )
    status = "passed" if ok else "failed"
    verification["ok"] = bool(ok)

    summary = (
        f"{status}：改动 {', '.join(change.path for change in changes)}；"
        f"目标测试 {len(target)} 条，回归 {len(after.passed - target)} 条通过"
    )
    state_store.save(
        {
            "kind": "run",
            "project": task.project,
            "task_id": task.id,
            "run_id": run_id,
            "status": status,
            "summary": summary,
            "provider": completion.provider,
            "model": completion.model,
            "diff_sha256": _sha256(diff_text),
            "changed_files": [change.path for change in changes],
            "tests": {
                "target": sorted(target),
                "failed_before": sorted(before.failed),
                "failed_after": sorted(after.failed),
                "passed_after": len(after.passed),
            },
            "context": {
                "spec": [hit.symbol for hit in pack.specs],
                "code": [hit.symbol for hit in pack.code],
                "tests": [hit.symbol for hit in pack.tests],
                "tokens": pack.tokens,
            },
        }
    )
    state_store.save(
        {
            "kind": "validation",
            "project": task.project,
            "task_id": task.id,
            "run_id": run_id,
            "status": status,
            "summary": f"目标测试 {len(target & after.passed)}/{len(target)} 通过；回归 {len(after.passed - target)} 条通过",
            "checks": verification,
        }
    )
    state_store.save(
        {
            "kind": "todo",
            "project": task.project,
            "task_id": task.id,
            "run_id": run_id,
            "status": "open" if status != "passed" else "closed",
            "summary": "T-002 规格变更复核（门槛 200→150）" if status == "passed" else "修复失败的目标测试",
            "items": [] if status == "passed" else sorted(target - after.passed),
        }
    )

    (run_dir / "db-queries.json").write_text(
        json.dumps(store.queries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = RunResult(
        run_id=run_id,
        run_dir=run_dir,
        status=status,
        verification=verification,
        context=pack,
        completion=completion,
        changes=changes,
        before=before,
        after=after,
    )
    (run_dir / "result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def render_status(records: list[dict[str, object]]) -> str:
    """Format the PowerContext journal for ``sddctx status``."""
    lines = ["# PowerContext 任务状态", ""]
    if not records:
        lines.append("（空）")
        return "\n".join(lines) + "\n"
    for record in records:
        kind = record.get("kind", "record")
        stamp = record.get("saved_at", "")
        task = record.get("task_id", "")
        status = record.get("status", "")
        summary = record.get("summary", "")
        lines.append(f"- `{stamp}` [{kind}] {task} {status} — {summary}")
        checks = record.get("checks")
        if isinstance(checks, dict):
            for key, value in checks.items():
                if value:
                    lines.append(f"  - {key}: {value}")
    return "\n".join(lines) + "\n"


def assert_clean_tests(workspace: Path, diff_text: str) -> None:
    """Defensive check used by tests: a diff may not touch the test suite."""
    for path in touched_paths(diff_text):
        if path.startswith("tests/"):
            raise PatchError(f"diff modifies protected test path: {path}")
