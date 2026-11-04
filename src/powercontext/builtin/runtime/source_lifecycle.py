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

"""Propagate Source withdrawal and restoration through an explicit dependency DAG.

Every Scope owns an append-only graph of registered Sources, Memory entries, and
Topic Memory artifacts. A Source event moves that Source between available and
unavailable; derived nodes are re-evaluated layer by layer under their ``all`` or
``any`` dependency policy. The resulting state is persisted, and the native
Memory projections are moved with it so list/search/context agree, while the
original registration reference stays readable for audit.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryEntryVersion
from powercontext.builtin.artifacts.topic_memory import TopicMemory
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.source_lifecycle import (
    MEMORY_KIND,
    SOURCE_KIND,
    TOPIC_KIND,
    LifecycleEventRecord,
    LifecycleNodeKind,
    LifecycleNodeRecord,
    LifecyclePolicy,
    LifecycleReference,
    SourceLifecycleRepository,
)
from powercontext.builtin.persistence.tables import MAX_LIFECYCLE_NODE_ID_LENGTH
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

LifecyclePurpose = Literal["generation", "audit"]
LIFECYCLE_PURPOSES: frozenset[str] = frozenset({"generation", "audit"})
_BLANK_MESSAGE = "value must not be blank"
_UNAVAILABLE_MESSAGE = "source lifecycle requires a configured database"


class SourceLifecycleError(ValueError):
    """Raised when a lifecycle registration, event, or lookup violates the contract."""

    def __init__(self, code: str, detail: object | None = None) -> None:
        self.code = code
        self.detail = detail
        messages = {
            "event": "lifecycle events must be SourceLifecycleEvent values",
            "source": "source registration requires a SourceRef",
            "revision": "source revision must be a positive integer",
            "memory": "memory registration requires a MemoryCitation",
            "topic": "topic registration requires an ArtifactRef",
            "topic-family": "topic registration requires a Topic Memory artifact",
            "node-id": "node ids must be non-empty strings",
            "node-id-long": f"node ids must not exceed {MAX_LIFECYCLE_NODE_ID_LENGTH} characters",
            "policy": "dependency policy must be 'all' or 'any'",
            "parents": "parents must be a sequence of node ids",
            "parents-empty": "derived nodes require at least one parent",
            "parent-id": "parent node ids must be non-empty strings",
            "parent-repeat": "parent node ids must not repeat",
            "self-cycle": "a lifecycle node cannot depend on itself",
            "missing-parent": "parents must be registered before their children",
            "binding": "the node is already registered with a different binding",
            "unknown-source": "the event does not name a registered Source",
            "unknown-node": "the lifecycle node is not registered in this Scope",
            "revision-mismatch": "the event revision does not match the registered revision",
            "sequence-older": "the event sequence is older than the accepted sequence",
            "sequence-conflict": "the event sequence was already accepted with a different state",
            "purpose": "unknown lifecycle purpose",
            "unavailable": "the lifecycle node is not usable for generation",
            "memory-missing": "the registered Memory citation is not readable in this Scope",
            "source-missing": "the registered Source is not readable in this Scope",
            "topic-missing": "the registered Topic Memory artifact is not readable in this Scope",
            "memory-entry": "the registered Memory entry is not in the current Memory revision",
        }
        message = messages.get(code, code)
        super().__init__(message if detail is None else f"{message}: {detail}")

    @classmethod
    def unreadable(cls, code: str, error: Exception) -> SourceLifecycleError:
        """Wrap one underlying lookup failure with its lifecycle meaning."""

        return cls(code, str(error))


class SourceLifecycleUnavailableError(RuntimeError):
    """Raised when the Runtime was assembled without durable lifecycle storage."""


class SourceLifecycleEvent(BaseModel):
    """One caller-supplied Source availability change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    reason: str
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool

    @field_validator("node_id", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(_BLANK_MESSAGE)
        return value


@dataclass(frozen=True, slots=True)
class TopicMemoryVisibility:
    """Usability snapshot of the Topic nodes registered for one Scope."""

    exact: Mapping[tuple[str, str, int], bool] = field(default_factory=dict)
    identities: Mapping[str, bool] = field(default_factory=dict)

    @property
    def restricted(self) -> bool:
        return bool(self.exact) or bool(self.identities)

    def allows(self, reference: ArtifactRef) -> bool:
        """Return whether one native Topic hit is still eligible for search/context."""

        exact = self.exact.get((reference.family, reference.artifact_id, reference.revision))
        if exact is not None:
            return exact
        return self.identities.get(reference.artifact_id, True)


@dataclass(slots=True)
class _ScopeState:
    nodes: dict[str, LifecycleNodeRecord] = field(default_factory=dict)
    available: dict[str, bool] = field(default_factory=dict)
    events: dict[str, LifecycleEventRecord] = field(default_factory=dict)
    loaded: bool = False

    def source_states(self, event: SourceLifecycleEvent) -> dict[str, bool]:
        states = {
            node_id: available
            for node_id, available in self.available.items()
            if self.nodes[node_id].kind == SOURCE_KIND
        }
        states[event.node_id] = event.available
        return states

    def usable_ids(self) -> list[str]:
        return sorted(node_id for node_id, available in self.available.items() if available)

    def memory_ids(self) -> frozenset[str]:
        return frozenset(node_id for node_id, node in self.nodes.items() if node.kind == MEMORY_KIND)


class ScopedSourceLifecycleService:
    """Register lifecycle nodes and apply Source events for one Scope."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = scope_id
        self._state = _ScopeState()
        self._lock = asyncio.Lock()

    # -- registration -----------------------------------------------------

    async def register_source(self, node_id: str, source_ref: SourceRef, revision: int) -> None:
        """Register one Source at an exact lifecycle revision."""

        _require_node_id(node_id)
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("revision", revision)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source")
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            self._lock,
        ):
            await self._load_locked()
            if self._is_replay(node_id, SOURCE_KIND, source_ref, (), None, revision):
                return
            await self._require_source_readable(source_ref)
            await self._insert_locked(
                node_id=node_id,
                kind=SOURCE_KIND,
                reference=source_ref,
                parents=(),
                policy=None,
                revision=revision,
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: Sequence[str],
        policy: LifecyclePolicy | str,
    ) -> None:
        """Register one Memory entry that is supported by ``parents``."""

        _require_node_id(node_id)
        normalized_policy = _require_policy(policy)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("memory")
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            self._lock,
        ):
            await self._load_locked()
            normalized_parents = self._require_parents(node_id, parents)
            if self._is_replay(node_id, MEMORY_KIND, citation, normalized_parents, normalized_policy, None):
                await self._sync_memory(self._single_node(node_id), reason="source lifecycle registration")
                return
            await self._require_memory_readable(citation)
            await self._insert_locked(
                node_id=node_id,
                kind=MEMORY_KIND,
                reference=citation,
                parents=normalized_parents,
                policy=normalized_policy,
                revision=None,
            )
            await self._sync_memory(self._single_node(node_id), reason="source lifecycle registration")

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        parents: Sequence[str],
        policy: LifecyclePolicy | str,
    ) -> None:
        """Register one Topic Memory artifact that is supported by ``parents``."""

        _require_node_id(node_id)
        normalized_policy = _require_policy(policy)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("topic")
        if artifact_ref.family != TopicMemory.family:
            raise SourceLifecycleError("topic-family")
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            self._lock,
        ):
            await self._load_locked()
            normalized_parents = self._require_parents(node_id, parents)
            if self._is_replay(node_id, TOPIC_KIND, artifact_ref, normalized_parents, normalized_policy, None):
                return
            await self._require_topic_readable(artifact_ref)
            await self._insert_locked(
                node_id=node_id,
                kind=TOPIC_KIND,
                reference=artifact_ref,
                parents=normalized_parents,
                policy=normalized_policy,
                revision=None,
            )

    # -- events -----------------------------------------------------------

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one Source event and return every currently usable node id, sorted."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event")
        async with (
            self._runtime._scope_operation(self.scope_id),
            self._runtime._locked(self.scope_id),
            self._lock,
        ):
            await self._load_locked()
            state = self._state
            replay = self._validate_event(state, event)
            derived = _derive_availability(state.nodes, state.source_states(event))
            changed = {
                node_id: available for node_id, available in derived.items() if state.available[node_id] != available
            }
            record = LifecycleEventRecord(
                node_id=event.node_id,
                sequence=event.sequence,
                revision=event.revision,
                available=event.available,
                reason=event.reason,
            )
            if not changed:
                if not replay:
                    await self._record_event(record)
                    state.events[event.node_id] = record
                return state.usable_ids()
            # Move the native projections before the lifecycle state becomes
            # authoritative: a failure then leaves the accepted state unchanged,
            # so retrying the same event re-derives and completes the propagation.
            memory_ids = state.memory_ids()
            memory_changes = {node_id: state.nodes[node_id] for node_id in changed if node_id in memory_ids}
            if memory_changes:
                await self._sync_memory(memory_changes, derived, reason=event.reason)
            await self._commit_change(changed, record)
            state.available.update(changed)
            state.events[event.node_id] = record
            return state.usable_ids()

    # -- references -------------------------------------------------------

    async def resolve(self, node_id: str, purpose: LifecyclePurpose | str) -> LifecycleReference:
        """Return the original registration reference when the purpose allows it."""

        if purpose not in LIFECYCLE_PURPOSES:
            raise SourceLifecycleError("purpose", purpose)
        async with self._lock:
            await self._load_locked()
            node = self._state.nodes.get(node_id)
            if node is None:
                raise SourceLifecycleError("unknown-node", node_id)
            if purpose == "generation" and not self._state.available[node_id]:
                raise SourceLifecycleError("unavailable", node_id)
            return node.reference

    async def topic_visibility(self) -> TopicMemoryVisibility:
        """Snapshot the registered Topic node states used to filter native hits."""

        async with self._lock:
            await self._load_locked()
            exact: dict[tuple[str, str, int], bool] = {}
            identities: dict[str, bool] = {}
            for node_id, node in self._state.nodes.items():
                if node.kind != TOPIC_KIND:
                    continue
                reference = cast(ArtifactRef, node.reference)
                usable = self._state.available[node_id]
                exact[(reference.family, reference.artifact_id, reference.revision)] = usable
                identities[reference.artifact_id] = identities.get(reference.artifact_id, False) or usable
            return TopicMemoryVisibility(exact=exact, identities=identities)

    # -- persistence ------------------------------------------------------

    async def _load_locked(self) -> None:
        if self._state.loaded:
            return
        async with self._database.transaction() as connection:
            nodes, events = await SourceLifecycleRepository(self.scope_id).load(connection)
        state = _ScopeState(events=events, loaded=True)
        for node in nodes:
            state.nodes[node.node_id] = node
            state.available[node.node_id] = node.available
        self._state = state

    async def _insert_locked(
        self,
        *,
        node_id: str,
        kind: LifecycleNodeKind,
        reference: LifecycleReference,
        parents: tuple[str, ...],
        policy: LifecyclePolicy | None,
        revision: int | None,
    ) -> None:
        state = self._state
        if kind == SOURCE_KIND:
            available = True
        else:
            support = [state.available[parent] for parent in parents]
            available = all(support) if policy == "all" else any(support)
        record = LifecycleNodeRecord(
            node_id=node_id,
            kind=kind,
            reference=reference,
            parents=parents,
            policy=policy,
            revision=revision,
            available=available,
            ordinal=len(state.nodes),
        )
        async with self._database.transaction() as connection:
            await SourceLifecycleRepository(self.scope_id).insert(connection, record)
        state.nodes[node_id] = record
        state.available[node_id] = available

    async def _commit_change(self, changed: Mapping[str, bool], record: LifecycleEventRecord) -> None:
        async with self._database.transaction() as connection:
            repository = SourceLifecycleRepository(self.scope_id)
            await repository.update_availability(connection, changed)
            if self._state.events.get(record.node_id) != record:
                await repository.record_event(connection, record)

    async def _record_event(self, record: LifecycleEventRecord) -> None:
        async with self._database.transaction() as connection:
            await SourceLifecycleRepository(self.scope_id).record_event(connection, record)

    # -- native Memory projection ----------------------------------------

    async def _sync_memory(
        self,
        nodes: Mapping[str, LifecycleNodeRecord],
        available: Mapping[str, bool] | None = None,
        *,
        reason: str,
    ) -> None:
        memory_nodes = [node for node in nodes.values() if node.kind == MEMORY_KIND]
        if not memory_nodes:
            return
        states = self._state.available if available is None else available
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await service.head(context.artifacts.memory_artifact_id)
            versions = {entry.entry_id: entry for entry in await service.entries(current)}
            activate: list[MemoryEntryVersion] = []
            deactivate: list[MemoryEntryVersion] = []
            for node in memory_nodes:
                citation = cast(MemoryCitation, node.reference)
                entry = versions.get(citation.entry_id)
                if entry is None:
                    raise SourceLifecycleError("memory-entry", node.node_id)
                (activate if states[node.node_id] else deactivate).append(entry)
            if deactivate:
                current = await service.forget(current, entries=tuple(deactivate), reason=reason)
            if activate:
                await service.reactivate(current, entries=tuple(activate), reason=reason)

    # -- validation -------------------------------------------------------

    def _single_node(self, node_id: str) -> dict[str, LifecycleNodeRecord]:
        return {node_id: self._state.nodes[node_id]}

    def _validate_event(self, state: _ScopeState, event: SourceLifecycleEvent) -> bool:
        node = state.nodes.get(event.node_id)
        if node is None or node.kind != SOURCE_KIND:
            raise SourceLifecycleError("unknown-source", event.node_id)
        if event.revision != node.revision:
            raise SourceLifecycleError("revision-mismatch", (event.revision, node.revision))
        previous = state.events.get(event.node_id)
        if previous is None:
            return False
        if event.sequence < previous.sequence:
            raise SourceLifecycleError("sequence-older", (event.sequence, previous.sequence))
        if event.sequence != previous.sequence:
            return False
        if event.available != previous.available:
            raise SourceLifecycleError("sequence-conflict", event.sequence)
        return True

    def _is_replay(
        self,
        node_id: str,
        kind: LifecycleNodeKind,
        reference: LifecycleReference,
        parents: tuple[str, ...],
        policy: LifecyclePolicy | None,
        revision: int | None,
    ) -> bool:
        existing = self._state.nodes.get(node_id)
        if existing is None:
            return False
        if (
            existing.kind == kind
            and existing.reference == reference
            and existing.parents == parents
            and existing.policy == policy
            and existing.revision == revision
        ):
            return True
        raise SourceLifecycleError("binding", node_id)

    def _require_parents(self, node_id: str, parents: Sequence[str]) -> tuple[str, ...]:
        if isinstance(parents, str):
            raise SourceLifecycleError("parents")
        normalized = tuple(parents)
        if not normalized:
            raise SourceLifecycleError("parents-empty")
        for parent in normalized:
            if not isinstance(parent, str) or not parent.strip():
                raise SourceLifecycleError("parent-id", parent)
        if len(set(normalized)) != len(normalized):
            raise SourceLifecycleError("parent-repeat")
        if node_id in normalized:
            raise SourceLifecycleError("self-cycle", node_id)
        missing = tuple(parent for parent in normalized if parent not in self._state.nodes)
        if missing:
            raise SourceLifecycleError("missing-parent", missing)
        return normalized

    async def _require_source_readable(self, reference: SourceRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                reference.source_type,
                reference.source_id,
            )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError.unreadable("source-missing", error) from error

    async def _require_memory_readable(self, citation: MemoryCitation) -> None:
        try:
            async with self._runtime._context(self.scope_id) as context:
                service = context.artifacts.memory
                memory = await service.revision(citation.memory_ref)
                entries = await service.entries(memory)
                artifact_id = context.artifacts.memory_artifact_id
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError.unreadable("memory-missing", error) from error
        if memory.artifact_id != artifact_id or not any(
            entry.entry_id == citation.entry_id and entry.entry_version_id == citation.entry_version_id
            for entry in entries
        ):
            raise SourceLifecycleError("memory-missing")

    async def _require_topic_readable(self, reference: ArtifactRef) -> None:
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                reference.family,
                reference.artifact_id,
                reference.revision,
            )
        except SourceLifecycleError:
            raise
        except Exception as error:
            raise SourceLifecycleError.unreadable("topic-missing", error) from error


class SourceLifecycleApplication:
    """Select the Source lifecycle service for one Scope."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase | None) -> None:
        self._runtime = runtime
        self._database = database
        self._services: dict[str, ScopedSourceLifecycleService] = {}

    @property
    def enabled(self) -> bool:
        """Whether this Runtime has durable lifecycle storage configured."""

        return self._database is not None

    def for_scope(self, scope_id: str) -> ScopedSourceLifecycleService:
        scope = validate_scope_id(scope_id)
        if self._database is None:
            raise SourceLifecycleUnavailableError(_UNAVAILABLE_MESSAGE)
        service = self._services.get(scope)
        if service is None:
            service = ScopedSourceLifecycleService(self._runtime, self._database, scope)
            self._services[scope] = service
        return service

    async def topic_visibility(self, scope_id: str) -> TopicMemoryVisibility | None:
        """Return the registered Topic node states, or ``None`` without storage."""

        if self._database is None:
            return None
        return await self.for_scope(scope_id).topic_visibility()


def _derive_availability(
    nodes: Mapping[str, LifecycleNodeRecord],
    sources: Mapping[str, bool],
) -> dict[str, bool]:
    """Recompute every node in registration order; parents always precede children."""

    derived: dict[str, bool] = {}
    for node_id, node in nodes.items():
        if node.kind == SOURCE_KIND:
            derived[node_id] = sources[node_id]
            continue
        support = [derived[parent] for parent in node.parents]
        derived[node_id] = all(support) if node.policy == "all" else any(support)
    return derived


def _require_node_id(node_id: object) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node-id")
    if len(node_id) > MAX_LIFECYCLE_NODE_ID_LENGTH:
        raise SourceLifecycleError("node-id-long")


def _require_policy(policy: object) -> LifecyclePolicy:
    if policy not in ("all", "any"):
        raise SourceLifecycleError("policy", policy)
    return cast(LifecyclePolicy, policy)


__all__ = [
    "ScopedSourceLifecycleService",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "SourceLifecycleUnavailableError",
    "TopicMemoryVisibility",
]
