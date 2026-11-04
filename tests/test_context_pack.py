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

import base64
import hashlib
import json

import pytest

from powercontext.builtin.runtime.context_pack import (
    DELTA_SCHEMA,
    PACK_SCHEMA,
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


def test_pack_wire_payload_matches_an_independent_oracle() -> None:
    items = (b"alpha\n", "海洋".encode())

    pack = ContextPack.build(items)
    payload = json.loads(pack.to_bytes())

    assert payload["schema"] == PACK_SCHEMA
    assert payload["items"] == [base64.b64encode(item).decode("ascii") for item in items]
    assert payload["byte_count"] == len(pack.to_bytes())
    unsigned = {"schema": PACK_SCHEMA, "items": payload["items"]}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    assert payload["digest"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert pack.to_bytes() == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def test_pack_round_trip_and_tamper_rejection() -> None:
    pack = ContextPack.build((b"alpha", b"beta"))

    assert ContextPack.from_bytes(pack.to_bytes()) == pack

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(pack.to_bytes().replace(pack.digest.encode(), b"sha256:" + b"0" * 64))
    payload = json.loads(pack.to_bytes())
    for field in payload:
        incomplete = {key: value for key, value in payload.items() if key != field}
        with pytest.raises(ContextPackError):
            ContextPack.from_bytes(json.dumps(incomplete).encode())


def test_from_content_chunks_within_bounds_and_keeps_bytes() -> None:
    text = "alpha 海洋 beta"

    pack = ContextPack.from_content(text, chunk_bytes=7)

    assert b"".join(pack.items).decode("utf-8") == text
    assert all(len(item) <= 7 for item in pack.items)
    for item in pack.items:
        item.decode("utf-8")
    with pytest.raises(ContextPackError):
        ContextPack.from_content("海", chunk_bytes=2)
    with pytest.raises(ContextPackError):
        ContextPack.from_content("x", chunk_bytes=0)


def test_empty_content_builds_an_empty_pack() -> None:
    empty = ContextPack.build(())

    assert ContextPack.from_content("") == empty
    assert ContextPack.from_bytes(empty.to_bytes()) == empty


@pytest.mark.parametrize(
    "target_items",
    [(b"a", b"b", b"new", b"tail"), (b"new", b"b", b"old"), (b"a", b"new", b"old"), (), (b"a", b"b", b"old")],
)
def test_delta_rebuilds_changes_anywhere(target_items: tuple[bytes, ...]) -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build(target_items)

    delta = ContextPackDelta.between(base, target)
    rebuilt = apply_context_pack_delta(
        ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
    )

    assert rebuilt.to_bytes() == target.to_bytes()
    assert delta.byte_count == len(delta.to_bytes())
    assert delta.prefix_items <= len(base.items)


def test_delta_rejects_wrong_base_and_out_of_range_prefix() -> None:
    base = ContextPack.build((b"one",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"one", b"two")))

    with pytest.raises(ContextPackError):
        apply_context_pack_delta(ContextPack.build((b"other",)), delta)
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, "sha256:" + "0" * 64, 2, ()))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, "sha256:" + "0" * 64, 0, (b"x",)))


@pytest.mark.parametrize("prefix", [True, 1.5, -1, "1"])
def test_delta_wire_rejects_invalid_prefix(prefix: object) -> None:
    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta")))
    payload = json.loads(delta.to_bytes())
    payload["prefix_items"] = prefix

    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def test_delta_wire_has_exactly_the_contract_fields() -> None:
    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta")))

    payload = json.loads(delta.to_bytes())

    assert set(payload) == {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
    assert payload["schema"] == DELTA_SCHEMA
    assert payload["base_digest"] == base.digest
    assert payload["prefix_items"] == 1
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps({**payload, "extra": 1}, sort_keys=True, separators=(",", ":")).encode())
