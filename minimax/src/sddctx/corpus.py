"""Scan a project into documents and retrievable chunks.

The corpus is intentionally structural rather than line-based: specifications are
split at clause headings, code at top-level symbols, and tests at test functions.
Each chunk keeps a stable, human-checkable source location so a later context pack
can cite ``path:start-end`` without guessing.
"""

from __future__ import annotations

import ast
import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

SPEC_KINDS = ("spec",)
CODE_KINDS = ("code", "test")

_CLAUSE_RE = re.compile(r"\b[A-Z]{2,}-[A-Z]{2,}-\d{3}\b")
_HEADING_RE = re.compile(r"^##\s+(?P<title>.+)$", re.MULTILINE)
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class Document:
    """One imported file."""

    doc_id: str
    project: str
    kind: str
    path: str
    version: str
    sha256: str
    text: str


@dataclass(frozen=True)
class Chunk:
    """One citable unit of a document."""

    chunk_id: str
    doc_id: str
    project: str
    kind: str
    path: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    tokens: int
    meta: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectCorpus:
    """Everything imported for one project in one import pass."""

    project: str
    spec_version: str
    documents: tuple[Document, ...]
    chunks: tuple[Chunk, ...]


def estimate_tokens(text: str) -> int:
    """Estimate prompt tokens: one per CJK character, four characters per token."""
    cjk = len(_CJK_RE.findall(text))
    rest = len(_CJK_RE.sub("", text))
    return cjk + math.ceil(rest / 4)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    values: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            values[key.strip().lower()] = value.strip()
    return values, text[match.end() :]


def _spec_chunks(
    project: str,
    path: Path,
    relative: str,
    text: str,
    version: str,
) -> tuple[Document, list[Chunk], str]:
    _, body = _front_matter(text)
    base_line = text[: len(text) - len(body)].count("\n")
    headings = list(_HEADING_RE.finditer(body))
    chunks: list[Chunk] = []
    if not headings:
        symbol = _CLAUSE_RE.search(body)
        clause = symbol.group(0) if symbol else f"{relative}#L1"
        chunks.append(
            _make_chunk(project, relative, "spec", clause, 1, body.count("\n") + 1, body.strip())
        )
    else:
        for index, heading in enumerate(headings):
            start = heading.start()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            section = body[start:end].strip("\n")
            title = heading.group("title").strip()
            clause_match = _CLAUSE_RE.search(title) or _CLAUSE_RE.search(section)
            clause = clause_match.group(0) if clause_match else f"{relative}#L{base_line + body[:start].count(chr(10)) + 1}"
            start_line = base_line + body[:start].count("\n") + 1
            end_line = start_line + section.count("\n")
            chunks.append(_make_chunk(project, relative, "spec", clause, start_line, end_line, section))
    document = _make_document(project, "spec", relative, version, text)
    return document, chunks, version


def _make_document(project: str, kind: str, relative: str, version: str, text: str) -> Document:
    return Document(
        doc_id=_stable_id(project, relative, kind),
        project=project,
        kind=kind,
        path=relative,
        version=version,
        sha256=_sha256(text),
        text=text,
    )


def _make_chunk(
    project: str,
    relative: str,
    kind: str,
    symbol: str,
    start_line: int,
    end_line: int,
    text: str,
    meta: dict[str, str] | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=_stable_id(project, relative, kind, symbol, str(start_line), text),
        doc_id=_stable_id(project, relative, kind),
        project=project,
        kind=kind,
        path=relative,
        symbol=symbol,
        start_line=max(1, start_line),
        end_line=max(start_line, end_line),
        text=text.strip("\n"),
        tokens=estimate_tokens(text),
        meta=meta or {},
    )


def _python_chunks(
    project: str,
    path: Path,
    relative: str,
    text: str,
    kind: str,
) -> tuple[Document, list[Chunk]]:
    lines = text.splitlines()
    tree = ast.parse(text, filename=str(path))
    module_path = relative[4:] if relative.startswith("src/") else relative
    module = module_path.removesuffix(".py").replace("/", ".")
    if module.endswith(".__init__"):
        module = module.removesuffix(".__init__")
    symbol_prefix = module if kind == "code" else relative
    chunks: list[Chunk] = []

    def add(node: ast.AST, name: str, start: int, end: int) -> None:
        body = "\n".join(lines[start - 1 : end])
        chunks.append(_make_chunk(project, relative, kind, name, start, end, body, {"node": type(node).__name__}))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            end = getattr(node, "end_lineno", node.lineno)
            join = "::" if kind == "test" else "."
            add(node, f"{symbol_prefix}{join}{node.name}", start, end)

    # Keep the module preamble (imports, constants, docstring) as its own chunk so
    # retrieval can cite shared settings without dragging in every function body.
    first_symbol_line = min(
        (
            min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ),
        default=len(lines) + 1,
    )
    preamble = "\n".join(lines[: first_symbol_line - 1]).strip("\n")
    if preamble:
        chunks.append(
            _make_chunk(project, relative, kind, f"{symbol_prefix}::<module>", 1, first_symbol_line - 1, preamble)
        )

    document = _make_document(project, kind, relative, "worktree", text)
    return document, chunks


def load_project(
    project_dir: Path,
    project: str,
    *,
    spec_version: str | None = None,
    spec_overlay: Path | None = None,
) -> ProjectCorpus:
    """Read specs, source and tests below ``project_dir`` into a fresh corpus."""
    specs_dir = project_dir / "specs"
    src_dir = project_dir / "src"
    tests_dir = project_dir / "tests"
    if not specs_dir.is_dir():
        raise FileNotFoundError(f"missing specs directory: {specs_dir}")

    documents: list[Document] = []
    chunks: list[Chunk] = []
    resolved_version = spec_version or "unversioned"

    for path in sorted(specs_dir.glob("*.md")):
        text = _read(path)
        if spec_overlay is not None and spec_overlay.name == path.name:
            text = _read(spec_overlay)
        meta, _ = _front_matter(text)
        version = spec_version or meta.get("spec-version", "unversioned")
        if resolved_version == "unversioned" and version != "unversioned":
            resolved_version = version
        document, spec_chunks, _ = _spec_chunks(
            project,
            path,
            path.relative_to(project_dir).as_posix(),
            text,
            version,
        )
        documents.append(document)
        chunks.extend(spec_chunks)

    for path in sorted(src_dir.rglob("*.py")) if src_dir.is_dir() else []:
        relative = path.relative_to(project_dir).as_posix()
        document, code_chunks = _python_chunks(project, path, relative, _read(path), "code")
        documents.append(document)
        chunks.extend(code_chunks)

    for path in sorted(tests_dir.rglob("*.py")) if tests_dir.is_dir() else []:
        relative = path.relative_to(project_dir).as_posix()
        document, test_chunks = _python_chunks(project, path, relative, _read(path), "test")
        documents.append(document)
        chunks.extend(test_chunks)

    if spec_overlay is not None and spec_version:
        resolved_version = spec_version

    return ProjectCorpus(
        project=project,
        spec_version=resolved_version,
        documents=tuple(documents),
        chunks=tuple(chunks),
    )
