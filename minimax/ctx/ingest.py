"""工程资料入库：文档 / 代码 / 测试的分块与元数据抽取。

分块策略（决定"找得准"）：
- Markdown：按标题层级切分，标题路径保留在 ``title`` 中；超长小节再按空行切分；
- Python：按 AST 切到函数/方法/类粒度，``symbol`` 记录 ``模块.类.函数`` 全名；
- JSON / 其他：整文件一块（测试数据集通常很小）。

元数据抽取：
- ``spec_codes``：文本中出现的 SPEC-/REQ-/AC-/BACKLOG- 编号，用于"规格—代码—测试"对齐；
- ``version``：优先取文档内 ``- 版本：`` 行，其次取 CLI 传入的版本号。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .store import Chunk, BaseStore, extract_spec_codes, make_chunk_id

VERSION_RE = re.compile(r"^-\s*版本[:：]\s*(\S+)", re.MULTILINE)
SUPPORTED_SUFFIXES = {".md", ".py", ".json", ".txt", ".sql", ".toml", ".ini", ".cfg", ".yml", ".yaml"}
MAX_CHUNK_CHARS = 2200


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf = ""
    for block in re.split(r"\n\s*\n", text):
        if len(buf) + len(block) + 2 > limit and buf:
            parts.append(buf.strip())
            buf = block
        else:
            buf = f"{buf}\n\n{block}" if buf else block
    if buf.strip():
        parts.append(buf.strip())
    return parts


def chunk_markdown(rel: str, text: str, version: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    h1 = ""
    h2 = ""
    buf: list[str] = []
    seq = 0

    def flush() -> None:
        nonlocal buf, seq
        body = "\n".join(buf).strip()
        buf = []
        if not body:
            return
        title = " / ".join(x for x in (h1, h2, h3) if x) or Path(rel).stem
        for piece in _split_long(body):
            seq += 1
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(rel, seq, piece),
                    doc_id=rel,
                    path=rel,
                    title=title,
                    kind="spec" if rel.startswith("spec/") else "doc",
                    text=f"{title}\n{piece}",
                    spec_codes=extract_spec_codes(f"{title}\n{piece}"),
                    version=version,
                    seq=seq,
                )
            )

    h3 = ""
    for line in text.splitlines():
        if line.startswith("### "):
            flush()
            h3 = line[4:].strip()
            buf.append(line)
        elif line.startswith("## "):
            flush()
            h2 = line[3:].strip()
            h3 = ""
            buf.append(line)
        elif line.startswith("# "):
            flush()
            h1 = line[2:].strip()
            h2 = ""
            h3 = ""
            buf.append(line)
        else:
            buf.append(line)
    flush()
    return chunks


def _python_symbols(tree: ast.AST, module: str) -> list[tuple[str, int, int]]:
    """返回 [(symbol, start_line, end_line)]，覆盖模块级函数与方法。"""
    out: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                out.append((name, child.lineno, getattr(child, "end_lineno", child.lineno)))
            elif isinstance(child, ast.ClassDef):
                name = f"{prefix}.{child.name}" if prefix else child.name
                out.append((name, child.lineno, getattr(child, "end_lineno", child.lineno)))
                visit(child, name)
            else:
                visit(child, prefix)

    visit(tree, module)
    return out


def chunk_python(rel: str, text: str, version: str) -> list[Chunk]:
    kind = "test" if ("tests/" in rel or Path(rel).name.startswith("test_")) else "code"
    module = Path(rel).stem
    chunks: list[Chunk] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        seq = 1
        return [
            Chunk(
                chunk_id=make_chunk_id(rel, seq, text),
                doc_id=rel,
                path=rel,
                title=f"{rel} (unparsed)",
                kind=kind,
                text=text[:MAX_CHUNK_CHARS],
                version=version,
                seq=seq,
            )
        ]

    lines = text.splitlines()
    header = "\n".join(lines[: min(len(lines), 25)])
    chunks.append(
        Chunk(
            chunk_id=make_chunk_id(rel, 0, header),
            doc_id=rel,
            path=rel,
            title=f"{rel} 模块头",
            kind=kind,
            text=header,
            symbol=module,
            spec_codes=extract_spec_codes(header),
            version=version,
            seq=0,
        )
    )
    for idx, (symbol, start, end) in enumerate(_python_symbols(tree, ""), start=1):
        body = "\n".join(lines[max(0, start - 1) : end])
        if not body.strip():
            continue
        chunks.append(
            Chunk(
                chunk_id=make_chunk_id(rel, idx, body),
                doc_id=rel,
                path=rel,
                title=f"{rel}::{symbol}",
                kind=kind,
                text=body,
                symbol=symbol,
                spec_codes=extract_spec_codes(body),
                version=version,
                seq=idx,
            )
        )
    return chunks


def chunk_text(rel: str, text: str, version: str) -> list[Chunk]:
    kind = "test" if "tests/" in rel else "doc"
    seq = 0
    out = []
    for piece in _split_long(text):
        seq += 1
        out.append(
            Chunk(
                chunk_id=make_chunk_id(rel, seq, piece),
                doc_id=rel,
                path=rel,
                title=rel,
                kind=kind,
                text=piece,
                spec_codes=extract_spec_codes(piece),
                version=version,
                seq=seq,
            )
        )
    return out


def chunk_file(path: Path, root: Path, version: str) -> list[Chunk]:
    rel = path.relative_to(root).as_posix()
    text = _read(path)
    detected = VERSION_RE.search(text)
    doc_version = detected.group(1) if detected else version
    if path.suffix == ".md":
        return chunk_markdown(rel, text, doc_version)
    if path.suffix == ".py":
        return chunk_python(rel, text, doc_version)
    return chunk_text(rel, text, doc_version)


def iter_files(root: Path, paths: Sequence[str], exclude_prefixes: Sequence[str]) -> Iterator[Path]:
    for raw in paths:
        p = (root / raw).resolve()
        if not p.exists():
            continue
        candidates = [p] if p.is_file() else sorted(p.rglob("*"))
        for f in candidates:
            if not f.is_file() or f.suffix not in SUPPORTED_SUFFIXES:
                continue
            rel = f.relative_to(root).as_posix()
            if any(rel.startswith(prefix) for prefix in exclude_prefixes):
                continue
            if "/.venv/" in f"/{rel}" or rel.startswith(".venv/"):
                continue
            yield f


def ingest(
    store: BaseStore,
    project_root: str | Path,
    paths: Sequence[str],
    version: str = "worktree",
    exclude_prefixes: Sequence[str] = ("spec/archive/", ".venv/"),
) -> dict:
    """把指定路径下的资料写入 store（同一 doc 覆盖式更新）。"""
    root = Path(project_root).resolve()
    docs = 0
    chunks = 0
    manifest: list[dict] = []
    for file in iter_files(root, paths, exclude_prefixes):
        chs = chunk_file(file, root, version)
        if not chs:
            continue
        store.replace_doc(chs[0].doc_id, chs)
        docs += 1
        chunks += len(chs)
        manifest.append(
            {
                "doc_id": chs[0].doc_id,
                "chunks": len(chs),
                "version": chs[0].version,
                "sha256": hashlib.sha256("".join(c.text for c in chs).encode()).hexdigest()[:16],
            }
        )
    return {"documents": docs, "chunks": chunks, "manifest": manifest}
