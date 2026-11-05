"""PowerContext-backed task state.

Every run appends one Memory entry in the default Scope. Entries are prefixed with
the literal token ``sdd`` so a single full-text recall returns the project journal,
and each entry body is JSON so the next run can reuse exact fields instead of
re-parsing prose.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from .errors import ConfigurationError, SddError

_MARKER = "sdd"
_SEPARATOR = " | "


def _import_runtime():
    try:
        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime import (
            BuiltinConfig,
            MemoryEntryInput,
            RememberMemoryRequest,
            SearchMemoryRequest,
            open_builtin_runtime,
        )
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ConfigurationError(
            "PowerContext is not installed; run `pip install 'powercontext[server]==1.2.0'`"
        ) from exc
    return {
        "SQLiteConfig": SQLiteConfig,
        "BuiltinConfig": BuiltinConfig,
        "MemoryEntryInput": MemoryEntryInput,
        "RememberMemoryRequest": RememberMemoryRequest,
        "SearchMemoryRequest": SearchMemoryRequest,
        "open_builtin_runtime": open_builtin_runtime,
    }


class StateStore:
    """Save and recall task progress and validation results."""

    def __init__(self, state_db: Path, scheduler_db: Path) -> None:
        self._state_db = state_db
        self._scheduler_db = scheduler_db
        self._modules = _import_runtime()

    @asynccontextmanager
    async def _memory(self) -> AsyncIterator[object]:
        modules = self._modules
        self._state_db.parent.mkdir(parents=True, exist_ok=True)
        url = "sqlite+aiosqlite:///" + self._state_db.as_posix()
        config = modules["BuiltinConfig"](database=modules["SQLiteConfig"](url=url))
        async with modules["open_builtin_runtime"](
            config, scheduler_path=str(self._scheduler_db)
        ) as runtime:
            scope = await runtime.scopes.bootstrap_default()
            yield runtime.memory.for_scope(scope.scope_id)

    async def _append(self, text: str) -> None:
        async with self._memory() as memory:
            await memory.remember(
                self._modules["RememberMemoryRequest"](
                    entries=(self._modules["MemoryEntryInput"](kind="sdd-state", text=text),)
                )
            )

    async def _recall(self) -> list[str]:
        async with self._memory() as memory:
            page = await memory.search(self._modules["SearchMemoryRequest"](query=_MARKER, limit=100))
            return [hit.text for hit in page.hits]

    def save(self, record: dict[str, object]) -> None:
        """Append one JSON record to the project journal."""
        payload = dict(record)
        payload.setdefault("saved_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        text = f"{_MARKER} {payload.get('kind', 'record')} {payload.get('project', '')}" + _SEPARATOR + json.dumps(
            payload, ensure_ascii=False, sort_keys=True
        )
        asyncio.run(self._append(text))

    def recall(self, project: str, *, task_id: str | None = None) -> list[dict[str, object]]:
        """Read the journal back; used by the next run and by ``status`` after restart."""
        try:
            texts = asyncio.run(self._recall())
        except SddError:
            raise
        records: list[dict[str, object]] = []
        for text in texts:
            _, separator, body = text.partition(_SEPARATOR)
            if not separator:
                continue
            try:
                record = json.loads(body)
            except json.JSONDecodeError:
                continue
            if record.get("project") != project:
                continue
            if task_id is not None and record.get("task_id") != task_id:
                continue
            records.append(record)
        records.sort(
            key=lambda item: (
                str(item.get("saved_at", "")),
                {"run": 0, "validation": 1, "todo": 2}.get(str(item.get("kind", "")), 3),
            )
        )
        return records

    def latest(self, project: str, task_id: str) -> dict[str, object] | None:
        records = self.recall(project, task_id=task_id)
        return records[-1] if records else None

    def delete_all(self) -> None:
        """Reset the journal by removing the SQLite file (module restart semantics)."""
        for path in (self._state_db, Path(str(self._state_db) + "-wal"), Path(str(self._state_db) + "-shm")):
            path.unlink(missing_ok=True)
        self._scheduler_db.unlink(missing_ok=True)
