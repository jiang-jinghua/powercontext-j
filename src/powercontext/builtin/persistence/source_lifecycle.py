# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable Source lifecycle registrations and per-Source event heads."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast

from pydantic import ValidationError
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.errors import InvalidStoredPayloadError
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.sources import SourceRef

LifecycleNodeKind = Literal["source", "memory", "topic"]
LifecyclePolicy = Literal["all", "any"]
LifecycleReference = SourceRef | MemoryCitation | ArtifactRef

SOURCE_KIND: Final = "source"
MEMORY_KIND: Final = "memory"
TOPIC_KIND: Final = "topic"
LIFECYCLE_NODE_KINDS: Final = frozenset({SOURCE_KIND, MEMORY_KIND, TOPIC_KIND})
LIFECYCLE_POLICIES: Final = frozenset({"all", "any"})
_REFERENCE_PAYLOAD = "lifecycle reference"
_NODE_PAYLOAD = "lifecycle node"


@dataclass(frozen=True, slots=True)
class LifecycleNodeRecord:
    """One immutable registration and its last derived usability state."""

    node_id: str
    kind: LifecycleNodeKind
    reference: LifecycleReference
    parents: tuple[str, ...]
    policy: LifecyclePolicy | None
    revision: int | None
    available: bool
    ordinal: int


@dataclass(frozen=True, slots=True)
class LifecycleEventRecord:
    """The latest accepted lifecycle event for one Source."""

    node_id: str
    sequence: int
    revision: int
    available: bool
    reason: str


class SourceLifecycleRepository:
    """Read and write the lifecycle rows owned by one Scope."""

    def __init__(self, scope_id: str) -> None:
        self._scope_id = scope_id

    async def load(
        self,
        connection: AsyncConnection,
        /,
    ) -> tuple[tuple[LifecycleNodeRecord, ...], dict[str, LifecycleEventRecord]]:
        nodes_table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = (
            (
                await connection.execute(
                    select(nodes_table).where(nodes_table.c.scope_id == self._scope_id).order_by(nodes_table.c.ordinal)
                )
            )
            .mappings()
            .all()
        )
        nodes = tuple(_node_from_row(row) for row in rows)
        events_table = SOURCE_LIFECYCLE_EVENTS_TABLE
        event_rows = (
            (await connection.execute(select(events_table).where(events_table.c.scope_id == self._scope_id)))
            .mappings()
            .all()
        )
        events = {str(row["node_id"]): _event_from_row(row) for row in event_rows}
        return nodes, events

    async def insert(self, connection: AsyncConnection, node: LifecycleNodeRecord, /) -> None:
        await connection.execute(
            insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                scope_id=self._scope_id,
                node_id=node.node_id,
                kind=node.kind,
                reference=dump_reference(node.reference),
                parents=json.dumps(list(node.parents), separators=(",", ":")).encode(),
                policy=node.policy,
                revision=node.revision,
                available=node.available,
                ordinal=node.ordinal,
            )
        )

    async def update_availability(self, connection: AsyncConnection, values: Mapping[str, bool], /) -> None:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        for node_id, available in values.items():
            await connection.execute(
                update(table)
                .where(
                    table.c.scope_id == self._scope_id,
                    table.c.node_id == node_id,
                )
                .values(available=available)
            )

    async def record_event(self, connection: AsyncConnection, event: LifecycleEventRecord, /) -> None:
        table = SOURCE_LIFECYCLE_EVENTS_TABLE
        keys = (table.c.scope_id == self._scope_id, table.c.node_id == event.node_id)
        exists = await connection.scalar(select(table.c.node_id).where(*keys))
        values = {
            "sequence": event.sequence,
            "revision": event.revision,
            "available": event.available,
            "reason": event.reason,
        }
        if exists is None:
            await connection.execute(insert(table).values(scope_id=self._scope_id, node_id=event.node_id, **values))
            return
        await connection.execute(update(table).where(*keys).values(**values))


def dump_reference(reference: LifecycleReference, /) -> bytes:
    """Encode one exact registration reference without losing its model type."""

    return reference.model_dump_json().encode("utf-8")


def load_reference(kind: str, payload: object, /) -> LifecycleReference:
    if not isinstance(payload, bytes):
        raise InvalidStoredPayloadError(_REFERENCE_PAYLOAD, kind, "payload column is not bytes")
    model = {SOURCE_KIND: SourceRef, MEMORY_KIND: MemoryCitation, TOPIC_KIND: ArtifactRef}.get(kind)
    if model is None:
        raise InvalidStoredPayloadError(_REFERENCE_PAYLOAD, kind, "unknown node kind")
    try:
        return cast(LifecycleReference, model.model_validate_json(payload))
    except ValidationError as error:
        raise InvalidStoredPayloadError(_REFERENCE_PAYLOAD, kind, "payload does not match its model") from error


def _node_from_row(row: Mapping[object, object]) -> LifecycleNodeRecord:
    node_id = str(row["node_id"])
    kind = str(row["kind"])
    if kind not in LIFECYCLE_NODE_KINDS:
        raise InvalidStoredPayloadError(_NODE_PAYLOAD, node_id, "unknown kind")
    policy = None if row["policy"] is None else str(row["policy"])
    if kind != SOURCE_KIND and policy not in LIFECYCLE_POLICIES:
        raise InvalidStoredPayloadError(_NODE_PAYLOAD, node_id, "unsupported policy")
    revision = None if row["revision"] is None else int(cast(int, row["revision"]))
    raw_parents = row["parents"]
    if not isinstance(raw_parents, bytes):
        raise InvalidStoredPayloadError(_NODE_PAYLOAD, node_id, "parents column is not bytes")
    try:
        decoded_parents = json.loads(raw_parents.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidStoredPayloadError(_NODE_PAYLOAD, node_id, "parents column is not JSON") from error
    if not isinstance(decoded_parents, list) or not all(isinstance(item, str) for item in decoded_parents):
        raise InvalidStoredPayloadError(_NODE_PAYLOAD, node_id, "parents column is not a string list")
    return LifecycleNodeRecord(
        node_id=node_id,
        kind=cast(LifecycleNodeKind, kind),
        reference=load_reference(kind, row["reference"]),
        parents=tuple(decoded_parents),
        policy=cast(LifecyclePolicy | None, policy),
        revision=revision,
        available=bool(row["available"]),
        ordinal=int(cast(int, row["ordinal"])),
    )


def _event_from_row(row: Mapping[object, object]) -> LifecycleEventRecord:
    return LifecycleEventRecord(
        node_id=str(row["node_id"]),
        sequence=int(cast(int, row["sequence"])),
        revision=int(cast(int, row["revision"])),
        available=bool(row["available"]),
        reason=str(row["reason"]),
    )


__all__ = [
    "LIFECYCLE_NODE_KINDS",
    "LIFECYCLE_POLICIES",
    "MEMORY_KIND",
    "SOURCE_KIND",
    "TOPIC_KIND",
    "LifecycleEventRecord",
    "LifecycleNodeKind",
    "LifecycleNodeRecord",
    "LifecyclePolicy",
    "LifecycleReference",
    "SourceLifecycleRepository",
    "dump_reference",
    "load_reference",
]
