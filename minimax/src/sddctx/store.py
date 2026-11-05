"""seekdb / OceanBase corpus store.

Two drivers are supported and share one SQL dialect:

* ``embedded`` — the ``pylibseekdb`` wheel starts a seekdb instance in-process and
  exposes a MySQL-compatible cursor. No server, no credentials.
* ``mysql`` — a running OceanBase / seekdb server reached over the MySQL protocol.

Both use seekdb's native full-text index (``WITH PARSER ik``) so retrieval is a
database query, not a Python scan.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from pymysql.converters import escape_string

from .corpus import Chunk, Document, ProjectCorpus
from .errors import ConfigurationError, StoreError

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS sdd_document (
        doc_id VARCHAR(64) NOT NULL PRIMARY KEY,
        project VARCHAR(64) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        path VARCHAR(512) NOT NULL,
        version VARCHAR(64) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        imported_at VARCHAR(40) NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_chunk (
        chunk_id VARCHAR(64) NOT NULL PRIMARY KEY,
        doc_id VARCHAR(64) NOT NULL,
        project VARCHAR(64) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        symbol VARCHAR(256) NOT NULL,
        path VARCHAR(512) NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        tokens INT NOT NULL,
        text LONGTEXT NOT NULL,
        FULLTEXT INDEX ft_chunk_text (text) WITH PARSER ik
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sdd_import (
        import_id VARCHAR(64) NOT NULL PRIMARY KEY,
        project VARCHAR(64) NOT NULL,
        spec_version VARCHAR(64) NOT NULL,
        documents INT NOT NULL,
        chunks INT NOT NULL,
        created_at VARCHAR(40) NOT NULL
    )
    """,
)

_CHUNK_COLUMNS = "chunk_id, doc_id, kind, symbol, path, start_line, end_line, tokens, text"


def quote(value: object) -> str:
    """Render a SQL string literal with MySQL escaping."""
    return "'" + escape_string(str(value)) + "'"


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk with its full-text score."""

    chunk_id: str
    doc_id: str
    kind: str
    symbol: str
    path: str
    start_line: int
    end_line: int
    tokens: int
    text: str
    score: float = 0.0

    @property
    def source(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


class _EmbeddedConnection:
    """Thin adapter over ``pylibseekdb`` (single process, no network)."""

    def __init__(self, path: Path) -> None:
        try:
            import pylibseekdb
        except ImportError as exc:  # pragma: no cover - platform dependent
            raise ConfigurationError(
                "pylibseekdb is not installed; install it (Linux/macOS) or set SDD_STORE=mysql"
            ) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        self._instance = pylibseekdb.open(str(path))
        self._connection = self._instance.connect("test", autocommit=False)
        self._cursor = self._connection.cursor()

    def execute(self, sql: str) -> list[tuple]:
        self._cursor.execute(sql)
        return list(self._cursor.fetchall())

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        try:
            self._cursor.close()
            self._connection.close()
        finally:
            self._instance.close()


class _MySQLConnection:
    """Thin adapter over ``pymysql`` for a running OceanBase/seekdb server."""

    def __init__(self) -> None:
        import pymysql

        self._pymysql = pymysql
        self._connection = pymysql.connect(
            host=os.environ.get("SDD_MYSQL_HOST", "127.0.0.1"),
            port=int(os.environ.get("SDD_MYSQL_PORT", "2881")),
            user=os.environ.get("SDD_MYSQL_USER", "root"),
            password=os.environ.get("SDD_MYSQL_PASSWORD", ""),
            database=os.environ.get("SDD_MYSQL_DATABASE", "sdd"),
            charset="utf8mb4",
            autocommit=False,
        )
        self._cursor = self._connection.cursor()

    def execute(self, sql: str) -> list[tuple]:
        self._cursor.execute(sql)
        return list(self._cursor.fetchall())

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._cursor.close()
        self._connection.close()


class CorpusStore:
    """Store and query the engineering corpus in seekdb."""

    def __init__(self, connection: _EmbeddedConnection | _MySQLConnection) -> None:
        self._connection = connection
        self.queries: list[dict[str, object]] = []

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def open(cls, *, db_path: Path | None = None, mode: str | None = None) -> CorpusStore:
        selected = (mode or os.environ.get("SDD_STORE", "embedded")).lower()
        if selected == "mysql":
            return cls(_MySQLConnection())
        if db_path is None:
            raise ConfigurationError("embedded seekdb needs a database path")
        return cls(_EmbeddedConnection(db_path))

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> CorpusStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- schema ------------------------------------------------------------
    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self._run(statement)
        self._connection.commit()

    def reset(self) -> None:
        for table in ("sdd_chunk", "sdd_document", "sdd_import"):
            self._run(f"DROP TABLE IF EXISTS {table}")
        self._connection.commit()
        self.ensure_schema()

    # -- writes ------------------------------------------------------------
    def replace_project(self, corpus: ProjectCorpus, *, spec_version: str | None = None) -> dict[str, object]:
        """Replace every row of one project in a single transaction."""
        project = quote(corpus.project)
        self._run(f"DELETE FROM sdd_chunk WHERE project = {project}")
        self._run(f"DELETE FROM sdd_document WHERE project = {project}")
        imported_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        version = spec_version or corpus.spec_version

        doc_values = [
            "("
            + ", ".join(
                (
                    quote(doc.doc_id),
                    quote(doc.project),
                    quote(doc.kind),
                    quote(doc.path),
                    quote(version),
                    quote(doc.sha256),
                    quote(imported_at),
                )
            )
            + ")"
            for doc in corpus.documents
        ]
        self._insert(
            "sdd_document(doc_id, project, kind, path, version, sha256, imported_at)",
            doc_values,
        )

        chunk_values = [
            "("
            + ", ".join(
                (
                    quote(chunk.chunk_id),
                    quote(chunk.doc_id),
                    quote(chunk.project),
                    quote(chunk.kind),
                    quote(chunk.symbol),
                    quote(chunk.path),
                    str(chunk.start_line),
                    str(chunk.end_line),
                    str(chunk.tokens),
                    quote(chunk.text),
                )
            )
            + ")"
            for chunk in corpus.chunks
        ]
        self._insert(
            "sdd_chunk(chunk_id, doc_id, project, kind, symbol, path, start_line, end_line, tokens, text)",
            chunk_values,
        )

        import_id = f"imp_{int(time.time())}"
        self._run(
            "INSERT INTO sdd_import(import_id, project, spec_version, documents, chunks, created_at) VALUES ("
            + ", ".join(
                (
                    quote(import_id),
                    project,
                    quote(version),
                    str(len(corpus.documents)),
                    str(len(corpus.chunks)),
                    quote(imported_at),
                )
            )
            + ")"
        )
        self._connection.commit()
        return {
            "import_id": import_id,
            "project": corpus.project,
            "spec_version": version,
            "documents": len(corpus.documents),
            "chunks": len(corpus.chunks),
            "imported_at": imported_at,
        }

    def _insert(self, target: str, values: list[str], batch_size: int = 100) -> None:
        for start in range(0, len(values), batch_size):
            batch = values[start : start + batch_size]
            if batch:
                self._run(f"INSERT INTO {target} VALUES " + ", ".join(batch))

    # -- reads -------------------------------------------------------------
    def search(self, project: str, query: str, limit: int = 40) -> list[Hit]:
        if not query.strip():
            return []
        sql = (
            f"SELECT {_CHUNK_COLUMNS}, MATCH(text) AGAINST ({quote(query)}) AS score "
            "FROM sdd_chunk "
            f"WHERE project = {quote(project)} AND MATCH(text) AGAINST ({quote(query)}) "
            "ORDER BY score DESC, chunk_id ASC "
            f"LIMIT {int(limit)}"
        )
        rows = self._run(sql)
        return [self._hit(row) for row in rows]

    def exact(
        self,
        project: str,
        *,
        symbols: tuple[str, ...] = (),
        paths: tuple[str, ...] = (),
    ) -> list[Hit]:
        clauses = []
        if symbols:
            clauses.append("symbol IN (" + ", ".join(quote(value) for value in symbols) + ")")
        if paths:
            clauses.append("path IN (" + ", ".join(quote(value) for value in paths) + ")")
        if not clauses:
            return []
        sql = (
            f"SELECT {_CHUNK_COLUMNS}, 0 AS score FROM sdd_chunk "
            f"WHERE project = {quote(project)} AND (" + " OR ".join(clauses) + ")"
        )
        return [self._hit(row) for row in self._run(sql)]

    def by_kind(self, project: str, kind: str, limit: int = 200) -> list[Hit]:
        sql = (
            f"SELECT {_CHUNK_COLUMNS}, 0 AS score FROM sdd_chunk "
            f"WHERE project = {quote(project)} AND kind = {quote(kind)} "
            f"ORDER BY path ASC, start_line ASC LIMIT {int(limit)}"
        )
        return [self._hit(row) for row in self._run(sql)]

    def stats(self, project: str) -> dict[str, object]:
        rows = self._run(
            "SELECT kind, COUNT(*) FROM sdd_chunk "
            f"WHERE project = {quote(project)} GROUP BY kind ORDER BY kind"
        )
        documents = self._run(f"SELECT COUNT(*) FROM sdd_document WHERE project = {quote(project)}")
        imports = self._run(
            "SELECT import_id, spec_version, documents, chunks, created_at FROM sdd_import "
            f"WHERE project = {quote(project)} ORDER BY created_at DESC LIMIT 1"
        )
        return {
            "documents": documents[0][0] if documents else 0,
            "chunks_by_kind": {row[0]: row[1] for row in rows},
            "last_import": (
                {
                    "import_id": imports[0][0],
                    "spec_version": imports[0][1],
                    "documents": imports[0][2],
                    "chunks": imports[0][3],
                    "created_at": imports[0][4],
                }
                if imports
                else None
            ),
        }

    def _hit(self, row: tuple) -> Hit:
        return Hit(
            chunk_id=row[0],
            doc_id=row[1],
            kind=row[2],
            symbol=row[3],
            path=row[4],
            start_line=int(row[5]),
            end_line=int(row[6]),
            tokens=int(row[7]),
            text=row[8],
            score=float(row[9] or 0.0),
        )

    # -- low level ---------------------------------------------------------
    def _run(self, sql: str) -> list[tuple]:
        started = time.perf_counter()
        try:
            rows = self._connection.execute(sql)
        except Exception as exc:
            self.queries.append(
                {
                    "sql_preview": sql[:400],
                    "rows": 0,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error": str(exc)[:300],
                }
            )
            raise StoreError(f"seekdb query failed: {exc}") from exc
        self.queries.append(
            {
                "sql_preview": sql[:400],
                "rows": len(rows),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return rows
