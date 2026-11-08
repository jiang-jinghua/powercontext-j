"""任务闭环：检索 → 生成 → 应用 → 回归 → 留痕 → 落状态。

单次 run 的产物全部落在 ``artifacts/runs/<run_id>/``：
- ``context.md`` / ``context.json``  本次使用的上下文包（规格/代码/测试/状态）
- ``prompt.md``                      实际发给 MiniMax 的提示词
- ``response_<n>.md``                模型原始回复
- ``patch.diff``                     从回复中抽取的补丁
- ``pytest.log``                     验收测试输出（修改后）
- ``result.json``                    汇总（含证据、测试计数、耗时、状态码）

``artifacts/records/minimax_calls.jsonl`` 逐条记录模型调用，供评审核对
"模型输出 → 补丁 → 代码差异"的对应关系。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import minimax, patch, state as state_mod
from .package import ContextPackage
from .retrieve import retrieve_for_task
from .store import get_store

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "artifacts" / "runs"
RECORDS = ROOT / "artifacts" / "records" / "minimax_calls.jsonl"

SYSTEM_PROMPT = """你是一名严格的 Python 后端工程师，遵循“最小改动 + 不破坏既有契约”的工程准则。
你将收到一个任务的规格条目、目标文件全文、相关代码、验证测试。
要求：
0. 先落笔再思考：第一行就开始写 ```diff 代码块；推理最多两三句，不要复述题目、不要罗列方案对比；
1. 只输出一个 ```` ```diff ```` 代码块，不要输出解释、不要输出完整文件；
2. 必须是 **git unified diff** 格式：每个文件以 `diff --git a/x b/x` 开头，紧随 `--- a/x` / `+++ b/x`，
   每个 hunk 头必须写全 `@@ -旧起点,旧行数 +新起点,新行数 @@`；
   **严禁**使用 `*** Update File:`、`*** Begin Patch`、只写 `@@` 的 Aider 风格，或任何非 git 格式；
3. diff 必须能对仓库当前状态 `git apply` 成功，路径使用仓库相对路径；
4. 不得修改或删除任何测试文件（tests/**），不得放宽断言；
5. 保持既有函数/类的公开签名与返回结构（只允许新增可选参数或字段）；
6. 所有金额计算必须保持整数分、精确无浮点误差；
7. 若规格之间有冲突，以最新版本规格为准，并在补丁中体现该选择。

补丁骨架（严格照此格式，行数必须与正文一致）：

```diff
diff --git a/src/settlement/engine.py b/src/settlement/engine.py
--- a/src/settlement/engine.py
+++ b/src/settlement/engine.py
@@ -10,3 +10,4 @@
 原有上下文行
-原行
+新行
```"""


@dataclass
class TestOutcome:
    ok: bool
    returncode: int
    passed: int
    failed: int
    log: str
    duration_s: float

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "passed": self.passed,
            "failed": self.failed,
            "duration_s": round(self.duration_s, 2),
            "tail": "\n".join(self.log.strip().splitlines()[-12:]),
        }


def _parse_pytest(log: str) -> tuple[int, int]:
    passed = failed = 0
    for line in log.splitlines()[::-1]:
        line = line.strip()
        if " passed" in line or " failed" in line or " error" in line:
            import re

            for num, word in re.findall(r"(\d+) (passed|failed|error|errors)", line):
                if word == "passed":
                    passed = int(num)
                else:
                    failed += int(num)
            if passed or failed:
                break
    return passed, failed


def snapshot_files(task: dict, root: Path) -> dict[str, str]:
    """记录目标文件在改动前的原文，便于整文件模式失败时回滚重试。"""
    snap: dict[str, str] = {}
    for rel in task.get("target_files", []):
        path = root / rel
        if path.is_file():
            snap[rel] = path.read_text(encoding="utf-8")
    return snap


def restore_files(snap: dict[str, str], root: Path) -> None:
    for rel, text in snap.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def syntax_check(root: Path, rels: list[str]) -> tuple[bool, str]:
    """整文件写入后先做语法闸门：坏文件不进测试，直接反馈重来。"""
    import ast

    for rel in rels:
        path = root / rel
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            return False, f"{rel}:{exc.lineno}: {exc.msg}\n{(exc.text or '').strip()}"
    return True, ""


def run_tests(task: dict, root: Path) -> TestOutcome:
    cmd = task.get("acceptance_cmd") or "python -m pytest -q"
    parts = cmd.split()
    if parts and parts[0] in ("python", "python3", "py"):
        parts[0] = sys.executable
    started = time.time()
    proc = subprocess.run(parts, cwd=str(root), capture_output=True, text=True, check=False)
    duration = time.time() - started
    log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    passed, failed = _parse_pytest(log)
    return TestOutcome(
        ok=proc.returncode == 0,
        returncode=proc.returncode,
        passed=passed,
        failed=failed,
        log=log,
        duration_s=duration,
    )


FILES_SYSTEM_PROMPT = """你是一名严格的 Python 后端工程师，遵循“最小改动 + 不破坏既有契约”的工程准则。
本次以**整文件模式**交付：对每个需要改动的文件，输出一个完整可运行的新版本。

要求：
1. 每个文件一个代码块，格式严格为 ```` ```file path=<仓库相对路径> ````，块内是该文件的**完整内容**；
2. 只输出需要改动的文件，未改动的文件不要输出；
3. 不得修改 tests/** 下任何文件；保持既有公开签名与返回结构（只允许新增可选参数或字段）；
4. 金额计算必须整数分、不得引入浮点误差；
5. 代码必须自洽可导入，不要写省略号、伪代码或 TODO。"""


def build_messages(package: ContextPackage, task: dict, feedback: str = "", mode: str = "diff") -> list[dict]:
    spec_block = "\n".join(f"- {i.code}（{i.path}，版本 {i.version}）" for i in package.spec_items)
    user = f"""# 任务
{task['task_id']} · {task['title']}

## 需求原文
{task.get('request', '')}

## 必须实现的规格条目（来自 seekdb 全文检索，版本 {package.spec_version}）
{spec_block}

## 上下文包（含目标文件全文；补丁必须基于真实内容构造 hunk，行号与缩进以它为准）
{package.to_markdown()}

## 验证方式
实现后必须让 `{task.get('acceptance_cmd', 'python -m pytest -q')}` 全绿，
其中 `tests/acceptance/` 是本任务的验收用例，`tests/test_*.py` 是必须保持通过的回归用例。
"""
    if feedback:
        user += f"""
## 上一次尝试失败（请据此修正，不要重复同样的错误）
```
{feedback[-4000:]}
```
"""
    if mode == "files":
        user += "\n请按 ```file path=... 的整文件格式输出需要改动的文件。"
        return [{"role": "system", "content": FILES_SYSTEM_PROMPT}, {"role": "user", "content": user}]
    user += "\n请直接输出 unified diff。"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def run_task(
    task_id: str,
    *,
    provider_name: str = "minimax",
    max_attempts: int = 3,
    root: str | Path | None = None,
    task_path: str | Path | None = None,
    skip_tests: bool = False,
    mode: str = "diff",
) -> dict:
    root = Path(root or ROOT)
    task_file = Path(task_path or root / "tasks" / f"{task_id}.json")
    task = json.loads(task_file.read_text(encoding="utf-8"))
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{task_id}"
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1) 状态恢复（跨会话）：先看本任务历史，再沿 parent_task 继承上游交接状态
    st = state_mod.get_state()
    prev = st.load(task_id) or {}
    parent_id = task.get("parent_task", "")
    parent = st.load(parent_id) if parent_id else None
    previous_done = list(prev.get("done", []))
    if parent:
        inherited = [f"{d}（继承自 {parent_id}）" for d in parent.get("done", [])]
        previous_done = list(dict.fromkeys(inherited + previous_done))
    handoff_text = state_mod.handoff_summary(parent) if parent else ""

    # 2) 检索 + 上下文包
    store = get_store()
    retrieval = retrieve_for_task(store, task)
    package = ContextPackage.build(
        task,
        retrieval,
        {
            "done": previous_done,
            "todo": task.get("followups", []),
            "risks": task.get("risks", []),
        },
    )
    package.state["parent_handoff"] = handoff_text
    package.save(run_dir)
    (run_dir / "retrieval.json").write_text(
        json.dumps(retrieval.queries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 3) 生成 → 应用 → 回归
    provider = minimax.get_provider(provider_name, task_id)
    attempts: list[dict] = []
    final: dict = {}
    feedback = ""
    applied_diff = ""
    test_outcome = TestOutcome(False, -1, 0, 0, "", 0.0)
    snap = snapshot_files(task, root) if mode == "files" else {}
    for attempt in range(1, max_attempts + 1):
        messages = build_messages(package, task, feedback, mode=mode)
        (run_dir / f"prompt_attempt{attempt}.md").write_text(
            "\n\n".join(m["content"] for m in messages), encoding="utf-8"
        )
        try:
            completion = provider.complete(messages, attempt=attempt)
        except minimax.MiniMaxError as exc:
            # 网络/截断/鉴权失败都要落进 attempts，不能让整条 run 直接崩掉
            attempts.append({"attempt": attempt, "status": "provider_error", "detail": str(exc)[:800]})
            feedback = f"上一次调用失败：{exc}"
            continue
        (run_dir / f"response_{attempt}.md").write_text(completion.text, encoding="utf-8")
        minimax.record_call(
            RECORDS,
            task_id=task_id,
            attempt=attempt,
            messages=messages,
            completion=completion,
        )
        try:
            diff = "" if mode == "files" else minimax.extract_diff(completion.text)
        except minimax.MiniMaxError as exc:
            feedback = f"输出格式不符合要求：{exc}"
            attempts.append({"attempt": attempt, "status": "no_diff", "detail": str(exc)})
            continue
        if mode == "files":
            try:
                files = minimax.extract_files(completion.text)
            except minimax.MiniMaxError as exc:
                feedback = f"整文件格式不符合要求：{exc}"
                attempts.append({"attempt": attempt, "status": "no_files", "detail": str(exc)})
                continue
            written, rejected = patch.write_files(root, files)
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "written" if written else "rejected",
                    "detail": f"写入 {len(written)} 个文件，拒绝 {rejected or '无'}",
                }
            )
            if not written:
                feedback = "整文件模式未能写入任何文件，请检查 ```file path=... 块"
                continue
            ok_syntax, syntax_detail = syntax_check(root, written)
            if not ok_syntax:
                restore_files(snap, root)
                attempts[-1].update({"status": "syntax_error", "detail": syntax_detail[:600]})
                feedback = f"写入的代码有语法错误，已回滚。请修正后重新输出完整文件：\n{syntax_detail}"
                continue
            attempt_diff = patch.diff_for(root, written)
            (run_dir / f"patch_attempt{attempt}.diff").write_text(attempt_diff, encoding="utf-8")
            applied_diff = attempt_diff
            test_outcome = run_tests(task, root)
            (run_dir / "pytest.log").write_text(test_outcome.log, encoding="utf-8")
            if test_outcome.ok:
                break
            restore_files(snap, root)
            feedback = test_outcome.log[-4000:]
            continue

        # 先做机械修复（重算 hunk 行数、清理围栏/Aider 标记），落盘的补丁与实际应用的一致
        diff, repairs = patch.repair_diff(diff)
        (run_dir / f"patch_attempt{attempt}.diff").write_text(diff, encoding="utf-8")
        non_git = any(line.strip() == "@@" for line in diff.splitlines())
        if non_git:
            feedback = (
                "补丁格式不合规：检测到 Aider 风格片段（*** Update File / 裸 @@）。"
                "请只输出标准 git unified diff：每文件 `diff --git a/… b/…` + `---`/`+++` + `@@ -a,b +c,d @@`。"
            )
            attempts.append({"attempt": attempt, "status": "bad_format", "detail": "检测到非 git 补丁格式"})
            continue
        result = patch.apply_patch(root, diff)
        attempts.append(
            {
                "attempt": attempt,
                "status": result["status"],
                "detail": result["detail"][:800],
                "repairs": repairs[:8],
            }
        )
        if result["status"] not in ("applied", "already_applied", "applied_repaired", "applied_by_patch"):
            feedback = f"补丁无法应用：{result['detail']}"
            continue
        applied_diff = diff
        if skip_tests:
            test_outcome = TestOutcome(True, 0, 0, 0, "(skipped)", 0.0)
            break
        test_outcome = run_tests(task, root)
        (run_dir / "pytest.log").write_text(test_outcome.log, encoding="utf-8")
        if test_outcome.ok:
            break
        feedback = test_outcome.log[-4000:]

    # 4) 汇总与落状态
    diff_now = patch.current_diff(root)
    (run_dir / "working_tree.diff").write_text(diff_now, encoding="utf-8")
    spec_codes = [i.code for i in package.spec_items]
    record = {
        "task_id": task_id,
        "task_title": task.get("title", ""),
        "run_id": run_id,
        "status": "done" if test_outcome.ok else "failed",
        "provider": provider.name,
        "model": getattr(provider, "model", ""),
        "mode": mode,
        "attempts": attempts,
        "iterations": len(attempts),
        "spec_version": package.spec_version,
        "spec_codes": spec_codes,
        "evidence": package.evidence,
        "patch_sha": __import__("hashlib").sha256(applied_diff.encode("utf-8")).hexdigest() if applied_diff else "",
        "patch_path": str((run_dir / "patch_attempt1.diff").relative_to(root)) if applied_diff else "",
        "tests": test_outcome.to_dict(),
        "done": [f"{c} 已实现（验收用例通过）" for c in spec_codes] if test_outcome.ok else previous_done,
        "todo": [] if test_outcome.ok else task.get("followups", []),
        "risks": task.get("risks", []),
        "store_backend": store.stats().get("backend"),
        "state_reused": (
            {
                "parent_task": parent_id,
                "parent_run_id": parent.get("run_id"),
                "parent_patch": parent.get("patch_sha", "")[:12],
                "parent_tests": parent.get("tests", {}),
            }
            if parent
            else {}
        ),
        "run_dir": str(run_dir.relative_to(root)),
    }
    final = record
    saved = st.save(task_id, record)
    record["state_backend"] = saved.get("backend")
    (run_dir / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def resume(task_id: str, *, root: str | Path | None = None) -> dict:
    """新会话接管：读状态 + 校验现场，返回可直接续跑的上下文。"""
    root = Path(root or ROOT)
    st = state_mod.get_state()
    record = st.load(task_id)
    store = get_store()
    stores = store.stats()
    spec_docs = {
        doc: ver for doc, ver in store.doc_versions().items() if doc.startswith("spec/")
    }
    task_file = root / "tasks" / f"{task_id}.json"
    parent_id = ""
    if task_file.exists():
        parent_id = json.loads(task_file.read_text(encoding="utf-8")).get("parent_task", "")
    parent = st.load(parent_id) if parent_id else None
    recall = st.recall(task_id) if hasattr(st, "recall") else []
    if not recall and parent_id and hasattr(st, "recall"):
        recall = st.recall(parent_id)
    return {
        "state": record,
        "handoff": state_mod.handoff_summary(record or {}),
        "powercontext_recall": recall,
        "parent_task": parent_id,
        "parent_handoff": state_mod.handoff_summary(parent) if parent else "",
        "store": stores,
        "spec_docs": spec_docs,
        "git_head": patch.head_sha(root),
        "dirty_files": patch.changed_files(root),
        "state_backend": getattr(st, "backend", "unknown"),
    }
