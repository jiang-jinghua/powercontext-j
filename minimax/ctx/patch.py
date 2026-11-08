"""补丁校验与应用（基于 git，保证可回滚、可复核）。

模型输出的补丁常见两类"技术上不合规但意图正确"的瑕疵，这里先做机械修复再看 git 意见：
1. hunk 头里的行数写错（``@@ -33,5 +34,8 @@`` 实际是 9 行）→ 按正文重算；
2. 代码块围栏残留或文件末尾漏换行 → 清理。
修复不改动补丁语义，只让 ``git apply`` 能读懂；修了什么会写进 ``repairs`` 供复核。
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
    )


def is_repo(root: str | Path) -> bool:
    return _git(Path(root), "rev-parse", "--is-inside-work-tree").returncode == 0


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def repair_diff(diff_text: str) -> tuple[str, list[str]]:
    """修掉模型补丁里的机械瑕疵，返回 (新补丁, 修复说明)。"""
    notes: list[str] = []
    raw = diff_text.replace("\r\n", "\n").split("\n")
    # 围栏与 Aider 标记（*** Begin Patch / *** Update File: / *** End Patch）都不是补丁内容
    lines = [ln for ln in raw if not ln.startswith("```") and not ln.startswith("*** ")]
    if len(lines) != len(raw):
        notes.append("移除代码块围栏残留")
    # 末尾空行不是 hunk 内容，留着会被当成一个上下文行，把行数算多 1
    trailing = 0
    while lines and lines[-1] == "":
        lines.pop()
        trailing += 1
    if trailing:
        notes.append("清理末尾空行")

    out: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = _HUNK_RE.match(line)
        if not match:
            out.append(line)
            idx += 1
            continue
        old_start, new_start, suffix = match.group(1), match.group(3), match.group(5)
        body: list[str] = []
        idx += 1
        while idx < len(lines) and not _HUNK_RE.match(lines[idx]) and not lines[idx].startswith("diff --git"):
            # 空行在 unified diff 里必须是"一个空格"，否则 git 直接判 corrupt
            body.append(" " if lines[idx] == "" else lines[idx])
            idx += 1
        old_count = sum(1 for b in body if b.startswith("-") or b.startswith(" ") or b == "")
        new_count = sum(1 for b in body if b.startswith("+") or b.startswith(" ") or b == "")
        rebuilt = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{suffix}"
        if rebuilt != line:
            notes.append(f"重算 hunk 行数：{line.strip()} → {rebuilt.strip()}")
        out.append(rebuilt)
        out.extend(body)
    return "\n".join(out).rstrip("\n") + "\n", notes  # 补一个换行，git 才认最后一行


def check_patch(root: str | Path, diff_text: str) -> tuple[bool, str]:
    root = Path(root)
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8", newline="\n") as fh:
        fh.write(diff_text)
        tmp = fh.name
    try:
        proc = _git(root, "apply", "--check", "--whitespace=nowarn", tmp)
        if proc.returncode == 0:
            return True, "ok"
        reverse = _git(root, "apply", "--check", "--reverse", "--whitespace=nowarn", tmp)
        if reverse.returncode == 0:
            return False, "already_applied"
        return False, (proc.stderr or proc.stdout).strip()
    finally:
        Path(tmp).unlink(missing_ok=True)


def apply_patch(root: str | Path, diff_text: str) -> dict:
    """应用补丁；若补丁已应用过则幂等返回。"""
    root = Path(root)
    repairs: list[str] = []
    ok, reason = check_patch(root, diff_text)
    if not ok and reason != "already_applied":
        fixed, notes = repair_diff(diff_text)
        if notes:
            ok_fixed, reason_fixed = check_patch(root, fixed)
            if ok_fixed:
                diff_text, ok, reason, repairs = fixed, True, "ok", notes
            elif reason_fixed == "already_applied":
                return {"applied": False, "status": "already_applied", "detail": "", "repairs": notes}
    if not ok:
        if reason == "already_applied":
            return {"applied": False, "status": "already_applied", "detail": ""}
        # git 对上下文极严格；模型补丁常只差几行偏移，用 GNU patch 的 fuzz 再试一次
        fuzzy = _apply_with_gnu_patch(root, diff_text)
        if fuzzy["status"] == "applied_by_patch":
            fuzzy["repairs"] = repairs
            return fuzzy
        return {"applied": False, "status": "rejected", "detail": reason, "repairs": repairs}
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8", newline="\n") as fh:
        fh.write(diff_text)
        tmp = fh.name
    try:
        proc = _git(root, "apply", "--whitespace=nowarn", tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if proc.returncode != 0:
        return {"applied": False, "status": "failed", "detail": (proc.stderr or proc.stdout).strip()}
    status = "applied_repaired" if repairs else "applied"
    return {"applied": True, "status": status, "detail": "", "repairs": repairs}


def _apply_with_gnu_patch(root: Path, diff_text: str) -> dict:
    """用 GNU patch 做 fuzz 兜底；出现 .rej 就算失败并回滚。"""
    import shutil

    if not shutil.which("patch"):
        return {"applied": False, "status": "rejected", "detail": "no patch binary"}
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8", newline="\n") as fh:
        fh.write(diff_text)
        tmp = fh.name
    try:
        proc = subprocess.run(
            ["patch", "-p1", "--fuzz=3", "--forward", "--batch", "-i", tmp],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    rejects = list(Path(root).rglob("*.rej"))
    if proc.returncode != 0 or rejects:
        for rej in rejects:
            rej.unlink(missing_ok=True)
        return {"applied": False, "status": "rejected", "detail": f"patch fuzz failed: {proc.stdout[-300:]}"}
    return {"applied": True, "status": "applied_by_patch", "detail": proc.stdout[-300:]}


def current_diff(root: str | Path, *, cached: bool = False) -> str:
    args = ["diff", "--no-color"] + (["--cached"] if cached else [])
    return _git(Path(root), *args).stdout


def diff_for(root: str | Path, paths: list[str]) -> str:
    """只取指定路径的差异（整文件模式下避免把无关改动卷进补丁留痕）。"""
    if not paths:
        return ""
    return _git(Path(root), "diff", "--no-color", "--", *paths).stdout


def changed_files(root: str | Path) -> list[str]:
    """工作区变更文件（已修改 + 已暂存 + 未跟踪），路径原样返回。"""
    root = Path(root)
    names: list[str] = []
    for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only"), ("ls-files", "--others", "--exclude-standard")):
        names.extend(line for line in _git(root, *args).stdout.splitlines() if line.strip())
    return sorted(dict.fromkeys(names))


def head_sha(root: str | Path) -> str:
    return _git(Path(root), "rev-parse", "--short", "HEAD").stdout.strip()


def write_files(root: str | Path, files: dict[str, str]) -> tuple[list[str], list[str]]:
    """整文件模式：只允许写 src/**，拒绝测试与仓库外路径。"""
    root = Path(root).resolve()
    written: list[str] = []
    rejected: list[str] = []
    for rel, content in files.items():
        target = (root / rel).resolve()
        if root not in target.parents or rel.startswith("tests/") or not rel.endswith(".py"):
            rejected.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        written.append(rel)
    return written, rejected
