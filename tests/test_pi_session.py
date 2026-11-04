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

from __future__ import annotations

import asyncio
import json

import pytest

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    ConnectorBinding,
    SourceDefinitionRegistry,
    SourceRef,
    TextEvidence,
    manifest_for_definition,
)
from powercontext.sources.connectors import ConnectorLifecycle
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiEventInput,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
    pi_event_source_id,
)

HEADER = {"type": "session", "version": 3, "id": "session-1"}


def _content(records: list[dict], *, header: dict | None = HEADER) -> bytes:
    rows = ([header] if header is not None else []) + records
    return "\n".join(json.dumps(row) for row in rows).encode()


def _parse(records: list[dict], *, leaf_id: str | None = None, header: dict | None = HEADER):
    return parse_pi_session(PiSessionInput(content=_content(records, header=header), leaf_id=leaf_id))


def test_parse_renders_each_visible_event_format() -> None:
    records = [
        {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "Request"}},
        {"type": "thinking_level_change", "id": "b", "parentId": "a"},
        {
            "type": "message",
            "id": "c",
            "parentId": "b",
            "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hidden"}]},
        },
        {
            "type": "message",
            "id": "d",
            "parentId": "c",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "first line"},
                    {"type": "image", "data": "ignored"},
                    {"type": "toolCall", "name": "read", "id": "call-1", "arguments": {"path": "a.txt", "page": 2}},
                ],
            },
        },
        {
            "type": "message",
            "id": "e",
            "parentId": "d",
            "message": {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "call-1",
                "content": [{"type": "text", "text": "file contents"}],
            },
        },
        {
            "type": "message",
            "id": "f",
            "parentId": "e",
            "message": {"role": "bashExecution", "command": "printf result", "output": "result", "content": []},
        },
        {"type": "compaction", "id": "g", "parentId": "f", "summary": "retained summary"},
        {"type": "branch_summary", "id": "h", "parentId": "g", "summary": "branch summary"},
        {
            "type": "custom_message",
            "id": "i",
            "parentId": "h",
            "content": [{"type": "text", "text": "Custom evidence"}],
        },
    ]

    events = _parse(records)

    assert [event.event_id for event in events] == ["a", "d", "e", "f", "g", "h", "i"]
    assert [event.content for event in events] == [
        "user: Request",
        'assistant: first line\n[tool_call read call-1] {"page":2,"path":"a.txt"}',
        "toolResult: [tool_result read call-1] file contents",
        "bashExecution: $ printf result\nresult",
        "compaction: retained summary",
        "branch_summary: branch summary",
        "custom: Custom evidence",
    ]
    assert events[1].parent_id == "c"
    assert events[4].event_type == "compaction" and events[4].role == "compaction"
    assert events[6].role == "custom"


def test_parse_selects_the_requested_branch_and_defaults_to_the_last_node() -> None:
    records = [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "root"}},
        {"type": "message", "id": "left", "parentId": "root", "message": {"role": "assistant", "content": "left"}},
        {"type": "message", "id": "right", "parentId": "root", "message": {"role": "assistant", "content": "right"}},
    ]

    assert [event.event_id for event in _parse(records, leaf_id="left")] == ["root", "left"]
    assert [event.event_id for event in _parse(records, leaf_id="right")] == ["root", "right"]
    assert [event.event_id for event in _parse(records)] == ["root", "right"]


def test_parse_accepts_a_bom_and_keeps_the_complete_record() -> None:
    records = [{"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "hi"}}]
    content = b"\xef\xbb\xbf" + _content(records)

    events = parse_pi_session(PiSessionInput(content=content))

    assert len(events) == 1
    assert events[0].record == records[0]
    assert events[0].session_id == "session-1"


@pytest.mark.parametrize(
    "records",
    [
        [{"type": "message", "id": "a", "parentId": "missing", "message": {"role": "user", "content": "x"}}],
        [{"type": "message", "id": "a", "parentId": "a", "message": {"role": "user", "content": "x"}}],
        [
            {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "x"}},
            {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "y"}},
        ],
        [{"type": "message", "parentId": None, "message": {"role": "user", "content": "x"}}],
        [{"type": "message", "id": "a", "message": {"role": "user", "content": "x"}}],
    ],
)
def test_parse_rejects_invalid_graphs(records: list[dict]) -> None:
    with pytest.raises(ValueError):
        _parse(records)


@pytest.mark.parametrize(
    "raw",
    [b"", b"{}\n", b"[]\n", b"null\n", b"\xff", b'{"type":"session","version":2,"id":"x"}\n'],
)
def test_parse_rejects_invalid_archives(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=raw))


def test_parse_rejects_a_missing_leaf() -> None:
    records = [{"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "x"}}]
    with pytest.raises(ValueError):
        _parse(records, leaf_id="absent")


def test_definition_reads_and_projects_the_complete_event() -> None:
    records = [
        {
            "type": "message",
            "id": "a",
            "parentId": None,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Answer"}]},
        }
    ]
    event = _parse(records)[0]
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))

    source = asyncio.run(registry.resolve(event))

    assert source.name == source.source_id == pi_event_source_id("session-1", "a")
    assert source.materialization.value == "captured"
    assert asyncio.run(registry.read(source)) == event
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence == TextEvidence(
        source_type="pi-event",
        source_id=source.name,
        content="assistant: Answer",
        metadata={
            "session_id": "session-1",
            "event_id": "a",
            "parent_id": None,
            "event_type": "message",
            "role": "assistant",
        },
    )
    assert manifest_for_definition(PI_EVENT_SOURCE_DEFINITION).name == "pi-event"


class _Sink:
    def __init__(self) -> None:
        self.submitted: list[PiEventInput] = []

    async def submit(self, binding, item_id, definition_name, value, /):
        self.submitted.append(value)
        return SourceRef(source_type=definition_name, source_id=pi_event_source_id(value.session_id, value.event_id))


class _Checkpoints:
    def __init__(self) -> None:
        self.saved = None

    async def load(self, binding, /):
        return self.saved

    async def save(self, binding, checkpoint, /, *, expected):
        assert checkpoint is not None and expected == self.saved
        self.saved = checkpoint


def _binding() -> ConnectorBinding:
    return ConnectorBinding(scope_id="s", binding_id="pi", connector_name="pi-session-history", connector_version="1")


def test_connector_imports_only_new_branch_events_and_rejects_rewrites(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    records = [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "root"}},
        {"type": "message", "id": "left", "parentId": "root", "message": {"role": "assistant", "content": "left"}},
        {"type": "model_change", "id": "control", "parentId": "root"},
        {"type": "message", "id": "right", "parentId": "control", "message": {"role": "assistant", "content": "right"}},
    ]
    path.write_bytes(_content(records))
    sink, checkpoints = _Sink(), _Checkpoints()
    lifecycle = ConnectorLifecycle(sink=sink, checkpoints=checkpoints)

    async def run(leaf: str):
        return await lifecycle.run(PiSessionConnector(path, leaf), _binding())

    asyncio.run(run("left"))
    assert [event.event_id for event in sink.submitted] == ["root", "left"]

    first = asyncio.run(run("right"))
    # Branch switching reopens root/control/right: only "right" is new.
    assert [outcome.item_id for outcome in first.items] == ["right"]

    second = asyncio.run(run("right"))
    assert second.items == ()

    records.append(
        {"type": "message", "id": "tail", "parentId": "right", "message": {"role": "assistant", "content": "tail"}}
    )
    path.write_bytes(_content(records))
    third = asyncio.run(run("tail"))
    assert len(third.items) == 1 and third.committed_checkpoint is not None

    path.write_bytes(_content(records) + b"{bad json}\n")
    with pytest.raises(ValueError):
        asyncio.run(run("tail"))

    path.write_bytes(_content(records))
    repaired = asyncio.run(run("tail"))
    assert repaired.items == () and repaired.previous_checkpoint == third.committed_checkpoint

    records[0]["message"]["content"] = "rewritten root"
    path.write_bytes(_content(records))
    with pytest.raises(ValueError):
        asyncio.run(run("tail"))
