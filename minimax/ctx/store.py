"""工程资料存储与全文检索。

两个实现：
- :class:`SeekdbStore`  —— seekdb / OceanBase（MySQL 协议），使用 FULLTEXT 索引 + ngram 分词，
  这是赛题要求的"工程资料存储与检索"主力实现；
- :class:`LocalFtsStore` —— SQLite FTS5，用于在无 seekdb 实例时保证工程可运行、测试可复现。

两者共用同一套 schema 语义与 :class:`Chunk` 数据模型，可通过
``CTX_STORE_BACKEND=seekdb|local`` 切换，检索结果结构与打分口径一致。
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

SPEC_CODE_RE = re.compile(r"(SPEC-\d{3}-\d{2}|REQ-\d{4}-\d{3}|AC-\d{2}|BACKLOG-\d{3})")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


# --------------------------------------------------------------------------- model


@dataclass
class Chunk:
    """一个可检索的上下文片段。"""

    chunk_id: str
    doc_id: str
    path: str
    title: str
    kind: str  # spec | code | test | doc
    text: str
    symbol: str = ""
    spec_codes: tuple[str, ...] = ()
    version: str = ""
    seq: int = 0


@dataclass
class Hit:
    """检索命中。"""

    chunk: Chunk
    score: float
    reason: str = ""
    meta: dict = field(default_factory=dict)


def make_chunk_id(doc_id: str, seq: int, text: str) -> str:
    digest = hashlib.sha1(f"{doc_id}:{seq}:{text}".encode("utf-8")).hexdigest()[:12]
    return f"{Path(doc_id).stem}#{seq}-{digest}"


def extract_spec_codes(text: str) -> tuple[str, ...]:
    seen: list[str] = []
    for code in SPEC_CODE_RE.findall(text):
        if code not in seen:
            seen.append(code)
    return tuple(seen)


def tokenize(text: str) -> str:
    """把文本切成空格分隔的词串，供全文索引使用。

    中文优先用 jieba 分词（与 seekdb ngram 分词口径接近），未安装时退化为
    汉字二元 + 英文标识符切分，保证零依赖也能用。
    """
    tokens: list[str] = []
    try:  # pragma: no cover - 依赖可选
        import jieba

        for tok in jieba.cut(text):
            tok = tok.strip().lower()
            if tok and not tok.isspace():
                tokens.append(tok)
    except Exception:  # pragma: no cover
        lowered = text.lower()
        cjk = re.findall(r"[\u4e00-\u9fff]+", lowered)
        for run in cjk:
            tokens.extend(run[i : i + 2] for i in range(max(1, len(run) - 1)))
        tokens.extend(m.lower() for m in IDENT_RE.findall(lowered))
    # 代码标识符单独补一遍（jieba 会把 snake_case 切碎）
    tokens.extend(m.lower() for m in IDENT_RE.findall(text))
    return " ".join(dict.fromkeys(tokens))


# --------------------------------------------------------------------------- base


class BaseStore:
    backend = "base"

    def init_schema(self) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def replace_doc(self, doc_id: str, chunks: Sequence[Chunk]) -> int:
        raise NotImplementedError

    def search(
        self, query: str, k: int = 8, kinds: Sequence[str] | None = None
    ) -> list[Hit]:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError

    def doc_versions(self) -> dict[str, str]:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _filter_kinds(kinds: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(kinds) if kinds else ()


def _rerank(hit: Hit, tokens: Sequence[str]) -> Hit:
    """可解释的重排：全文召回之后，用"覆盖度 + 结构信号"重新打分。

    打分口径（写入留痕，便于评审复核为什么是这几条）：
    - 正文覆盖：命中的不同查询词占查询词总数的比例 × 3.0（主信号）
    - 标题覆盖：同上 × 1.0
    - 词频：正文出现次数（截断 5）× 0.1
    - 规格编码精确命中：+6.0（强意图，如 "SPEC-011-02"）
    - 结构：spec 片段 +0.5；带小节路径的片段 +0.3（比根 section 更具体）
    """
    body = hit.chunk.text.split("\n", 1)[-1].lower()
    title = hit.chunk.title.lower()
    lowered = [t.lower() for t in tokens if t]
    total = max(1, len(lowered))

    cov_body = sum(1 for t in lowered if t in body) / total
    cov_title = sum(1 for t in lowered if t in title) / total
    tf = sum(min(body.count(t), 5) for t in lowered)
    code_hit = 6.0 if any(c.lower() in lowered for c in hit.chunk.spec_codes) else 0.0
    structure = (0.5 if hit.chunk.kind == "spec" else 0.0) + (0.3 if " / " in hit.chunk.title else 0.0)

    score = 3.0 * cov_body + 1.0 * cov_title + 0.1 * tf + code_hit + structure
    hit.meta = {
        "fts_score": round(hit.score, 4),
        "coverage_body": round(cov_body, 3),
        "coverage_title": round(cov_title, 3),
        "tf": tf,
        "structure": round(structure, 2),
    }
    hit.score = round(score, 4)
    return hit


# --------------------------------------------------------------------------- local


class LocalFtsStore(BaseStore):
    """SQLite FTS5 实现（无外部依赖，便于复现）。"""

    backend = "local-fts5"

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS ctx_doc (
              doc_id TEXT PRIMARY KEY,
              path TEXT NOT NULL,
              title TEXT,
              kind TEXT,
              version TEXT,
              sha256 TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS ctx_chunk (
              chunk_id TEXT PRIMARY KEY,
              doc_id TEXT NOT NULL,
              path TEXT NOT NULL,
              title TEXT,
              kind TEXT,
              symbol TEXT,
              spec_codes TEXT,
              version TEXT,
              seq INTEGER,
              text TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS ctx_fts USING fts5(
              chunk_id UNINDEXED,
              tokens,
              tokenize = 'unicode61'
            );
            CREATE INDEX IF NOT EXISTS idx_chunk_doc ON ctx_chunk(doc_id);
            CREATE INDEX IF NOT EXISTS idx_chunk_kind ON ctx_chunk(kind);
            """
        )
        self.conn.commit()

    def replace_doc(self, doc_id: str, chunks: Sequence[Chunk]) -> int:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM ctx_fts WHERE chunk_id IN (SELECT chunk_id FROM ctx_chunk WHERE doc_id = ?)", (doc_id,))
        cur.execute("DELETE FROM ctx_chunk WHERE doc_id = ?", (doc_id,))
        payload = []
        for ch in chunks:
            cur.execute(
                """INSERT OR REPLACE INTO ctx_chunk
                   (chunk_id, doc_id, path, title, kind, symbol, spec_codes, version, seq, text)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    ch.chunk_id,
                    ch.doc_id,
                    ch.path,
                    ch.title,
                    ch.kind,
                    ch.symbol,
                    ",".join(ch.spec_codes),
                    ch.version,
                    ch.seq,
                    ch.text,
                ),
            )
            payload.append((ch.chunk_id, tokenize(f"{ch.title}\n{ch.symbol}\n{ch.text}")))
        cur.executemany("INSERT INTO ctx_fts (chunk_id, tokens) VALUES (?,?)", payload)
        version = chunks[0].version if chunks else ""
        cur.execute(
            """INSERT OR REPLACE INTO ctx_doc (doc_id, path, title, kind, version, sha256, updated_at)
               VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                doc_id,
                chunks[0].path if chunks else doc_id,
                chunks[0].title if chunks else doc_id,
                chunks[0].kind if chunks else "doc",
                version,
                hashlib.sha256("".join(c.text for c in chunks).encode("utf-8")).hexdigest()[:16],
            ),
        )
        self.conn.commit()
        return len(chunks)

    def search(self, query: str, k: int = 8, kinds: Sequence[str] | None = None) -> list[Hit]:
        tokens = [t for t in tokenize(query).split(" ") if len(t) >= 1]
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens[:24])
        sql = """
            SELECT c.*, bm25(ctx_fts) AS rank
            FROM ctx_fts JOIN ctx_chunk c ON c.chunk_id = ctx_fts.chunk_id
            WHERE ctx_fts MATCH ?
        """
        params: list = [match]
        kf = _filter_kinds(kinds)
        if kf:
            sql += f" AND c.kind IN ({','.join('?' for _ in kf)})"
            params.extend(kf)
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(k * 6, 24))
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        hits = []
        for row in rows:
            ch = Chunk(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                path=row["path"],
                title=row["title"],
                kind=row["kind"],
                text=row["text"],
                symbol=row["symbol"],
                spec_codes=tuple(x for x in (row["spec_codes"] or "").split(",") if x),
                version=row["version"],
                seq=row["seq"],
            )
            hit = Hit(chunk=ch, score=-float(row["rank"]), reason="bm25(fts5)→rerank")
            hits.append(_rerank(hit, tokens))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def stats(self) -> dict:
        docs = self.conn.execute("SELECT COUNT(*) AS n FROM ctx_doc").fetchone()["n"]
        chunks = self.conn.execute("SELECT COUNT(*) AS n FROM ctx_chunk").fetchone()["n"]
        by_kind = {
            row["kind"]: row["n"]
            for row in self.conn.execute("SELECT kind, COUNT(*) AS n FROM ctx_chunk GROUP BY kind")
        }
        return {"backend": self.backend, "documents": docs, "chunks": chunks, "by_kind": by_kind}

    def doc_versions(self) -> dict[str, str]:
        return {
            row["doc_id"]: row["version"]
            for row in self.conn.execute("SELECT doc_id, version FROM ctx_doc")
        }


# --------------------------------------------------------------------------- seekdb

SEEKDB_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS ctx_doc (
        doc_id   VARCHAR(255) PRIMARY KEY,
        path     VARCHAR(512) NOT NULL,
        title    VARCHAR(255),
        kind     VARCHAR(16),
        version  VARCHAR(64),
        sha256   VARCHAR(64),
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS ctx_chunk (
        chunk_id   VARCHAR(128) PRIMARY KEY,
        doc_id     VARCHAR(255) NOT NULL,
        path       VARCHAR(512) NOT NULL,
        title      VARCHAR(255),
        kind       VARCHAR(16),
        symbol     VARCHAR(255),
        spec_codes VARCHAR(255),
        version    VARCHAR(64),
        seq        INT,
        text       MEDIUMTEXT,
        tokens     MEDIUMTEXT,
        FULLTEXT INDEX ft_ctx_chunk_tokens (tokens) WITH PARSER ngram,
        INDEX idx_ctx_chunk_doc (doc_id),
        INDEX idx_ctx_chunk_kind (kind)
    )""",
]


class SeekdbStore(BaseStore):
    """seekdb / OceanBase 实现：FULLTEXT 索引（ngram）+ MATCH AGAINST 检索。"""

    backend = "seekdb"

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        import pymysql  # 延迟导入，local 模式无需该依赖

        self.database = database or os.environ.get("SEEKDB_DB", "ctx")
        connect_kwargs = dict(
            host=host or os.environ.get("SEEKDB_HOST", "127.0.0.1"),
            port=int(port or os.environ.get("SEEKDB_PORT", "2881")),
            user=user or os.environ.get("SEEKDB_USER", "root"),
            password=password if password is not None else os.environ.get("SEEKDB_PASSWORD", ""),
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.conn = pymysql.connect(**connect_kwargs)
        # 数据库不存在则自动创建（seekdb 默认只有 test 库）
        with self.conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
            cur.execute(f"USE `{self.database}`")
        self.init_schema()

    def init_schema(self) -> None:
        self.fts_mode = "ngram"
        with self.conn.cursor() as cur:
            for ddl in SEEKDB_SCHEMA:
                try:
                    cur.execute(ddl)
                except Exception as exc:  # 已存在索引/表时忽略
                    if "exist" in str(exc).lower():
                        continue
                    # ngram 解析器不可用：退化为普通 FULLTEXT，再失败则用 LIKE 检索
                    if "ft_ctx_chunk_tokens" in ddl:
                        try:
                            cur.execute(ddl.replace(" WITH PARSER ngram", ""))
                            self.fts_mode = "default"
                            continue
                        except Exception:
                            self.fts_mode = "like"
                            continue
                    raise

    def replace_doc(self, doc_id: str, chunks: Sequence[Chunk]) -> int:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM ctx_chunk WHERE doc_id = %s", (doc_id,))
            if chunks:
                cur.executemany(
                    """INSERT INTO ctx_chunk
                       (chunk_id, doc_id, path, title, kind, symbol, spec_codes, version, seq, text, tokens)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE text=VALUES(text), tokens=VALUES(tokens)""",
                    [
                        (
                            c.chunk_id,
                            c.doc_id,
                            c.path,
                            c.title,
                            c.kind,
                            c.symbol,
                            ",".join(c.spec_codes),
                            c.version,
                            c.seq,
                            c.text,
                            tokenize(f"{c.title}\n{c.symbol}\n{c.text}"),
                        )
                        for c in chunks
                    ],
                )
            cur.execute(
                """INSERT INTO ctx_doc (doc_id, path, title, kind, version, sha256, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,NOW())
                   ON DUPLICATE KEY UPDATE version=VALUES(version), sha256=VALUES(sha256),
                                          updated_at=NOW(), title=VALUES(title)""",
                (
                    doc_id,
                    chunks[0].path if chunks else doc_id,
                    chunks[0].title if chunks else doc_id,
                    chunks[0].kind if chunks else "doc",
                    chunks[0].version if chunks else "",
                    hashlib.sha256("".join(c.text for c in chunks).encode("utf-8")).hexdigest()[:16],
                ),
            )
        return len(chunks)

    def search(self, query: str, k: int = 8, kinds: Sequence[str] | None = None) -> list[Hit]:
        tokens = [t for t in tokenize(query).split(" ") if t][:24]
        if not tokens:
            return []
        match_expr = " ".join(tokens)
        sql = """SELECT chunk_id, doc_id, path, title, kind, symbol, spec_codes, version, seq, text,
                        MATCH(tokens) AGAINST (%s) AS score
                 FROM ctx_chunk WHERE MATCH(tokens) AGAINST (%s)"""
        params: list = [match_expr, match_expr]
        kf = _filter_kinds(kinds)
        if kf:
            sql += f" AND kind IN ({','.join('%s' for _ in kf)})"
            params.extend(kf)
        sql += " ORDER BY score DESC LIMIT %s"
        params.append(max(k * 6, 24))
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception:
            # FULLTEXT 不可用（未建索引/解析器缺失）时退化为多词 LIKE 召回
            rows = self._like_search(tokens, k, kinds)
        hits = []
        for row in rows:
            ch = Chunk(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                path=row["path"],
                title=row["title"],
                kind=row["kind"],
                text=row["text"],
                symbol=row["symbol"] or "",
                spec_codes=tuple(x for x in (row["spec_codes"] or "").split(",") if x),
                version=row["version"] or "",
                seq=row["seq"] or 0,
            )
            hit = Hit(chunk=ch, score=float(row["score"] or 0), reason="match-against(ngram)→rerank")
            hits.append(_rerank(hit, tokens))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def _like_search(self, tokens: Sequence[str], k: int, kinds: Sequence[str] | None) -> list[dict]:
        kf = _filter_kinds(kinds)
        where = " AND ".join("(text LIKE %s OR tokens LIKE %s)" for _ in tokens)
        params: list = []
        for tok in tokens:
            params.extend([f"%{tok}%", f"%{tok}%"])
        sql = f"""SELECT chunk_id, doc_id, path, title, kind, symbol, spec_codes, version, seq, text,
                         0 AS score FROM ctx_chunk WHERE {where}"""
        if kf:
            sql += f" AND kind IN ({','.join('%s' for _ in kf)})"
            params.extend(kf)
        sql += " LIMIT %s"
        params.append(max(k * 6, 24))
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def stats(self) -> dict:
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM ctx_doc")
            docs = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM ctx_chunk")
            chunks = cur.fetchone()["n"]
            cur.execute("SELECT kind, COUNT(*) AS n FROM ctx_chunk GROUP BY kind")
            by_kind = {r["kind"]: r["n"] for r in cur.fetchall()}
        return {
            "backend": self.backend,
            "database": self.database,
            "fts_mode": getattr(self, "fts_mode", "unknown"),
            "documents": docs,
            "chunks": chunks,
            "by_kind": by_kind,
        }

    def doc_versions(self) -> dict[str, str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT doc_id, version FROM ctx_doc")
            return {r["doc_id"]: r["version"] for r in cur.fetchall()}


# --------------------------------------------------------------------------- factory


def get_store(backend: str | None = None, *, project_root: str | Path | None = None) -> BaseStore:
    """按配置返回存储实例。

    ``backend`` 优先级：显式参数 > ``CTX_STORE_BACKEND`` 环境变量 > ``seekdb``（若可连通）> ``local``。
    """
    name = (backend or os.environ.get("CTX_STORE_BACKEND") or "").strip().lower()
    root = Path(project_root or Path(__file__).resolve().parent.parent)
    if name in ("", "auto"):
        try:
            store = SeekdbStore()
            store.stats()
            return store
        except Exception:
            return LocalFtsStore(root / "artifacts" / "ctx_local.db")
    if name in ("seekdb", "oceanbase", "mysql"):
        return SeekdbStore()
    if name in ("local", "local-fts5", "sqlite"):
        return LocalFtsStore(root / "artifacts" / "ctx_local.db")
    raise ValueError(f"unknown CTX_STORE_BACKEND: {name}")
