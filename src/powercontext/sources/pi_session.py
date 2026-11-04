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

"""Capture the visible events of one selected Pi session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME: Final = "pi-event"
PI_SESSION_CONNECTOR_NAME: Final = "pi-session-history"
PI_EVENT_SOURCE_VERSION: Final = "1"

_SESSION_TYPE: Final = "session"
_SESSION_VERSION: Final = 3
_IDENTITY_SCHEME: Final = "pi"
_CHECKPOINT_VERSION: Final = 1
_UNKNOWN: Final = "unknown"
_UNSAFE: Final = ""

_MESSAGE_TYPE: Final = "message"
_COMPACTION_TYPE: Final = "compaction"
_BRANCH_SUMMARY_TYPE: Final = "branch_summary"
_CUSTOM_MESSAGE_TYPE: Final = "custom_message"

_TOOL_RESULT_ROLE: Final = "toolResult"
_BASH_ROLE: Final = "bashExecution"
_CUSTOM_ROLE: Final = "custom"

_TEXT_BLOCK: Final = "text"
_TOOL_CALL_BLOCK: Final = "toolCall"


class PiSessionInput(BaseModel):
    """One complete Pi JSONL archive and an optional selected branch leaf."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event from a Pi session branch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured evidence for exactly one Pi session event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return the visible events of the selected branch, ordered root to leaf."""

    records = _load_records(value.content)
    session_id = _session_id(records[0])
    nodes = _index_nodes(records[1:])
    leaf_id = value.leaf_id if value.leaf_id is not None else next(reversed(nodes), None)
    if leaf_id is None:
        return ()
    events = []
    for event_id in _branch_ids(nodes, leaf_id):
        event = _visible_event(session_id, event_id, nodes[event_id])
        if event is not None:
            events.append(event)
    return tuple(events)


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the delimiter-safe identity of one Pi session event."""

    for name, component in (("session_id", session_id), ("event_id", event_id)):
        if not isinstance(component, str) or not component:
            raise ValueError(f"{name} must be a non-empty string")  # noqa: TRY003
    identity = (
        f"{_IDENTITY_SCHEME}:{quote(session_id, safe=_UNSAFE)}:{quote(event_id, safe=_UNSAFE)}"
    )
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"Pi event Source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )
    return identity


class PiEventAdapter:
    """Resolve and read the captured Pi event Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            source_id=identity,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose the complete event text as canonical evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PiEventAdapter(),
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import each not yet captured event of the selected Pi branch exactly once."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_EVENT_SOURCE_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        imported = _read_checkpoint(session.checkpoint)
        digests = {event.event_id: _record_digest(event.record) for event in events}
        for event_id, digest in imported.items():
            current = digests.get(event_id)
            if current is not None and current != digest:
                raise ValueError(f"captured Pi event {event_id!r} changed")  # noqa: TRY003
        pending = [event for event in events if event.event_id not in imported]
        for event in pending:
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
        checkpoint = dict(imported)
        checkpoint.update({event.event_id: digests[event.event_id] for event in pending})
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": _CHECKPOINT_VERSION, "events": checkpoint},
        )


def _load_records(content: bytes) -> tuple[dict[str, JsonValue], ...]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be strict UTF-8 with an optional leading BOM") from error
    records: list[dict[str, JsonValue]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed Pi session record: {error}") from error
        if not isinstance(record, dict):
            raise ValueError("Pi session records must be JSON objects")  # noqa: TRY003
        records.append(record)
    if not records:
        raise ValueError("Pi session content must contain a session header")  # noqa: TRY003
    return tuple(records)


def _session_id(header: dict[str, JsonValue]) -> str:
    if header.get("type") != _SESSION_TYPE or header.get("version") != _SESSION_VERSION:
        raise ValueError("Pi session header must declare type 'session' and version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must contain a non-empty id")  # noqa: TRY003
    return session_id


def _index_nodes(records: tuple[dict[str, JsonValue], ...]) -> dict[str, dict[str, JsonValue]]:
    nodes: dict[str, dict[str, JsonValue]] = {}
    for record in records:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Pi session records must contain a non-empty id")  # noqa: TRY003
        if event_id in nodes:
            raise ValueError(f"Pi session repeats node id {event_id!r}")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError(f"Pi session node {event_id!r} must declare parentId")  # noqa: TRY003
        parent_id = record["parentId"]
        if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
            raise ValueError(f"Pi session node {event_id!r} has an invalid parentId")  # noqa: TRY003
        if parent_id is not None and parent_id not in nodes:
            raise ValueError(  # noqa: TRY003
                f"Pi session node {event_id!r} refers to a parent that has not appeared"
            )
        nodes[event_id] = record
    return nodes


def _branch_ids(nodes: dict[str, dict[str, JsonValue]], leaf_id: str) -> list[str]:
    if leaf_id not in nodes:
        raise ValueError(f"Pi session leaf {leaf_id!r} does not exist")  # noqa: TRY003
    ordered: list[str] = []
    visited: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in visited:
            raise ValueError("Pi session branch contains a cycle")  # noqa: TRY003
        visited.add(current)
        ordered.append(current)
        parent = nodes[current]["parentId"]
        current = parent if isinstance(parent, str) else None
    ordered.reverse()
    return ordered


def _visible_event(session_id: str, event_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = record.get("type")
    if not isinstance(event_type, str):
        return None
    if event_type == _MESSAGE_TYPE:
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role:
            return None
        body = _message_body(role, message)
    elif event_type in {_COMPACTION_TYPE, _BRANCH_SUMMARY_TYPE}:
        role = event_type
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else None
    elif event_type == _CUSTOM_MESSAGE_TYPE:
        role = _CUSTOM_ROLE
        body = _content_text(record.get("content"))
    else:
        return None
    if not body:
        return None
    parent_id = record.get("parentId")
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id if isinstance(parent_id, str) else None,
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _message_body(role: str, message: dict[str, JsonValue]) -> str | None:
    if role == _TOOL_RESULT_ROLE:
        text = _content_text(message.get("content"))
        if text is None:
            return None
        tool_name = _text_or_unknown(message.get("toolName"))
        tool_call_id = _text_or_unknown(message.get("toolCallId"))
        return f"[tool_result {tool_name} {tool_call_id}] {text}"
    if role == _BASH_ROLE:
        command = message.get("command")
        if not isinstance(command, str):
            return None
        output = message.get("output")
        return f"$ {command}\n{output if isinstance(output, str) else ''}"
    return _content_text(message.get("content"))


def _content_text(content: JsonValue) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == _TEXT_BLOCK:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == _TOOL_CALL_BLOCK:
            parts.append(_tool_call_text(block))
    if not parts:
        return None
    return "\n".join(parts)


def _tool_call_text(block: dict[str, JsonValue]) -> str:
    name = _text_or_unknown(block.get("name"))
    call_id = _text_or_unknown(block.get("id"))
    arguments = block.get("arguments")
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("Pi tool call arguments must be valid JSON") from error
    return f"[tool_call {name} {call_id}] {encoded}"


def _text_or_unknown(value: JsonValue) -> str:
    return value if isinstance(value, str) and value else _UNKNOWN


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _read_checkpoint(value: JsonValue) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or value.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Pi session checkpoint has an unsupported shape")  # noqa: TRY003
    events = value.get("events")
    if not isinstance(events, dict):
        raise ValueError("Pi session checkpoint must retain imported event digests")  # noqa: TRY003
    imported: dict[str, str] = {}
    for event_id, digest in events.items():
        if not isinstance(event_id, str) or not isinstance(digest, str):
            raise ValueError("Pi session checkpoint entries must map ids to digests")  # noqa: TRY003
        imported[event_id] = digest
    return imported


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_SOURCE_VERSION",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventAdapter",
    "PiEventInput",
    "PiEventSource",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
