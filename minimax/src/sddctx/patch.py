"""Parse model output into an applicable unified diff without shelling out to ``git``.

Models are much more reliable at returning whole files than at hand-computing
``@@`` hunk offsets, so the default path converts ``### FILE:`` sections into a
diff locally; the strict diff parser remains available for models that emit one.
"""

from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import PatchError

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FENCE_RE = re.compile(r"^```(?:diff|patch)?\s*$")
_FILE_HEADER_RE = re.compile(r"^#{2,4}\s*FILE:\s*(?P<path>[^\s`]+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class FileChange:
    """One file touched by a diff."""

    path: str
    added: int
    removed: int
    created: bool = False


@dataclass(frozen=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[tuple[str, str], ...]  # (op, text), op in " ", "-", "+"


def extract_diff(text: str) -> str:
    """Return the unified diff embedded in a model reply."""
    stripped = text.replace("\r\n", "\n").strip("\n")
    fenced = re.search(r"```(?:diff|patch)?\n(.*?)```", stripped, re.DOTALL)
    candidate = fenced.group(1) if fenced else stripped
    lines = candidate.splitlines()
    start = None
    for index in range(len(lines) - 1):
        if lines[index].startswith("--- ") and lines[index + 1].startswith("+++ "):
            start = index
            break
    if start is None:
        raise PatchError("model output does not contain a unified diff (`--- a/...` / `+++ b/...`)")
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("```") and index > start:
            end = index
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _clean_path(raw: str) -> str:
    value = raw.split("\t")[0].strip()
    if value.startswith(("a/", "b/")):
        value = value[2:]
    if value == "/dev/null":
        return value
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise PatchError(f"diff touches a path outside the workspace: {value}")
    return str(path)


def _parse(diff_text: str) -> list[tuple[str, str, list[_Hunk]]]:
    lines = diff_text.splitlines()
    files: list[tuple[str, str, list[_Hunk]]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("--- "):
            index += 1
            continue
        old_path = _clean_path(line[4:])
        if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
            raise PatchError("unified diff header is missing `+++`")
        new_path = _clean_path(lines[index + 1][4:])
        index += 2
        hunks: list[_Hunk] = []
        while index < len(lines) and not lines[index].startswith("--- "):
            if not lines[index].startswith("@@"):
                index += 1
                continue
            match = _HUNK_RE.match(lines[index])
            if not match:
                raise PatchError(f"bad hunk header: {lines[index][:80]}")
            old_start = int(match.group(1))
            old_count = int(match.group(2) or 1)
            new_start = int(match.group(3))
            new_count = int(match.group(4) or 1)
            index += 1
            body: list[tuple[str, str]] = []
            while index < len(lines) and not lines[index].startswith(("@@", "--- ")):
                raw = lines[index]
                if raw.startswith("diff --git"):
                    break
                if raw.startswith("\\"):
                    index += 1
                    continue
                if raw == "":
                    body.append((" ", ""))
                elif raw[0] in " +-":
                    body.append((raw[0], raw[1:]))
                else:
                    body.append((" ", raw))
                index += 1
            hunks.append(_Hunk(old_start, old_count, new_start, new_count, tuple(body)))
        files.append((old_path, new_path, hunks))
    if not files:
        raise PatchError("no file sections found in the diff")
    return files


def touched_paths(diff_text: str) -> list[str]:
    return [new for _, new, _ in _parse(extract_diff(diff_text)) if new != "/dev/null"]


def extract_file_sections(reply: str) -> dict[str, str]:
    """Parse ``### FILE: path`` sections with one fenced block each."""
    matches = list(_FILE_HEADER_RE.finditer(reply))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        path = match.group("path").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(reply)
        block = reply[start:end]
        fence = re.search(r"```[^\n]*\n(?P<body>.*?)```", block, re.DOTALL)
        body = fence.group("body") if fence else block.strip("\n")
        body = body.rstrip("\n") + "\n"
        if not body.strip():
            raise PatchError(f"empty file section for {path}")
        sections[path] = body
    return sections


def diff_from_files(
    reply: str,
    workspace: Path,
    *,
    allowed_prefixes: tuple[str, ...] = ("src/",),
    forbidden_prefixes: tuple[str, ...] = ("tests/",),
) -> str:
    """Turn full-file sections into a unified diff against the workspace."""
    sections = extract_file_sections(reply)
    if not sections:
        raise PatchError("model reply has no `### FILE: <path>` sections")
    patches: list[str] = []
    for path, new_text in sections.items():
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts:
            raise PatchError(f"file section escapes the workspace: {path}")
        if any(path.startswith(prefix) for prefix in forbidden_prefixes):
            raise PatchError(f"file section touches a protected path: {path}")
        if not any(path.startswith(prefix) for prefix in allowed_prefixes):
            raise PatchError(f"file section is outside the allowed area: {path}")
        source = workspace / path
        if not source.is_file():
            raise PatchError(f"file section references a missing file: {path}")
        old_text = source.read_text(encoding="utf-8")
        if old_text == new_text:
            continue
        if path.endswith(".py"):
            try:
                ast.parse(new_text)
            except SyntaxError as exc:
                raise PatchError(f"file section for {path} is not valid Python: {exc}") from exc
        patches.append(
            "".join(
                difflib.unified_diff(
                    old_text.splitlines(keepends=True),
                    new_text.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    n=3,
                )
            )
        )
    if not patches:
        raise PatchError("model reply did not change any file")
    return "".join(patches)


def apply_patch(
    diff_text: str,
    workspace: Path,
    *,
    forbidden_prefixes: tuple[str, ...] = ("tests/",),
) -> list[FileChange]:
    """Apply ``diff_text`` to ``workspace`` and return the changed files."""
    files = _parse(extract_diff(diff_text))
    for old_path, new_path, _ in files:
        for path in (old_path, new_path):
            if path == "/dev/null":
                continue
            if any(path.startswith(prefix) for prefix in forbidden_prefixes):
                raise PatchError(f"diff tries to modify a protected path: {path}")
    changes: list[FileChange] = []
    for old_path, new_path, hunks in files:
        target = workspace / new_path
        if old_path == "/dev/null":
            source_lines: list[str] = []
            created = True
        else:
            source = workspace / old_path
            if not source.is_file():
                raise PatchError(f"diff references a missing file: {old_path}")
            source_lines = source.read_text(encoding="utf-8").splitlines()
            created = False
        applied, added, removed = _apply_hunks(source_lines, hunks, new_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(applied) + "\n", encoding="utf-8")
        changes.append(FileChange(path=new_path, added=added, removed=removed, created=created))
    return changes


def _apply_hunks(
    source: list[str], hunks: list[_Hunk], path: str
) -> tuple[list[str], int, int]:
    result: list[str] = []
    cursor = 0
    added = removed = 0
    for hunk in hunks:
        expected = [text for op, text in hunk.lines if op in " -"]
        position = _locate(source, expected, hunk.old_start - 1)
        if position is None:
            raise PatchError(
                f"hunk at {path}:{hunk.old_start} does not match the file ({len(expected)} context lines)"
            )
        if position < cursor:
            raise PatchError(f"overlapping hunks at {path}:{hunk.old_start}")
        result.extend(source[cursor:position])
        cursor = position
        for op, text in hunk.lines:
            if op == " ":
                if text != source[cursor]:
                    raise PatchError(f"context mismatch at {path}:{cursor + 1}")
                result.append(source[cursor])
                cursor += 1
            elif op == "-":
                if text != source[cursor]:
                    raise PatchError(f"removed line mismatch at {path}:{cursor + 1}")
                cursor += 1
                removed += 1
            else:
                result.append(text)
                added += 1
    result.extend(source[cursor:])
    return result, added, removed


def _locate(source: list[str], expected: list[str], hint: int) -> int | None:
    if not expected:
        return max(0, min(hint, len(source)))
    if hint < 0:
        hint = 0
    candidates = sorted(
        list(range(max(0, hint - 120), min(len(source), hint + 120))) + [hint],
        key=lambda value: (abs(value - hint), value),
    )
    for start in candidates:
        if start + len(expected) > len(source):
            continue
        if source[start : start + len(expected)] == expected:
            return start
    return None
