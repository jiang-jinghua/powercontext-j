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

"""Prefix-reusing wire format for prepared context.

The first request sends a complete pack; later requests send a delta that names the
base pack it extends, how many leading base chunks the receiver keeps, and the target
suffix. A receiver rebuilds the target from the base and the delta alone.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Final

PACK_SCHEMA: Final = "powercontext.context-pack.v1"
DELTA_SCHEMA: Final = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES: Final = 512

_PACK_FIELDS: Final = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS: Final = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(Exception):
    """Raised when a pack or delta violates the wire protocol."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One immutable, self-describing sequence of UTF-8 text chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...] | list[bytes], /) -> ContextPack:
        """Build the complete pack for one exact chunk sequence."""

        chunks = tuple(_as_bytes(item) for item in items)
        digest = _pack_digest(chunks)
        count = 0
        for _attempt in range(8):
            payload = {"schema": PACK_SCHEMA, "items": _encode_items(chunks), "digest": digest, "byte_count": count}
            encoded = _canonical(payload)
            if len(encoded) == count:
                return cls(chunks, digest, count)
            count = len(encoded)
        raise ContextPackError("pack byte_count did not converge")  # noqa: TRY003

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Split one prepared context into deterministic, self-contained UTF-8 chunks."""

        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        text = _as_text(content)
        chunks: list[bytes] = []
        current = b""
        for character in text:
            encoded = character.encode("utf-8")
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")  # noqa: TRY003
            if len(current) + len(encoded) > chunk_bytes:
                chunks.append(current)
                current = b""
            current += encoded
        if current:
            chunks.append(current)
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        """Serialize the complete pack to its canonical wire form."""

        return _canonical(
            {
                "schema": PACK_SCHEMA,
                "items": _encode_items(self.items),
                "digest": self.digest,
                "byte_count": self.byte_count,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode and fully validate one complete pack."""

        payload = _load_object(data)
        _require_fields(payload, _PACK_FIELDS, "pack")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("pack schema is not supported")  # noqa: TRY003
        items = _decode_items(payload["items"], "items")
        digest = _require_digest(payload["digest"], "digest")
        if digest != _pack_digest(items):
            raise ContextPackError("pack digest does not match its items")  # noqa: TRY003
        count = payload["byte_count"]
        if type(count) is not int or count != len(data):
            raise ContextPackError("pack byte_count does not match the wire payload")  # noqa: TRY003
        return cls(items, digest, count)


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """One delta that rebuilds a target pack from an identified base pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Reuse the longest common chunk prefix and carry the target suffix."""

        prefix = 0
        for left, right in zip(base.items, target.items):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        """Size of the serialized delta, including every protocol field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize the delta to its canonical wire form."""

        return _canonical(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": _encode_items(self.appended),
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode and fully validate one delta."""

        payload = _load_object(data)
        _require_fields(payload, _DELTA_FIELDS, "delta")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("delta schema is not supported")  # noqa: TRY003
        base_digest = _require_digest(payload["base_digest"], "base_digest")
        target_digest = _require_digest(payload["target_digest"], "target_digest")
        prefix = payload["prefix_items"]
        if type(prefix) is not int or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        appended = _decode_items(payload["appended"], "appended")
        return cls(base_digest, target_digest, prefix, appended)


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a base pack and one validated delta."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta does not extend the supplied base pack")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base chunk count")  # noqa: TRY003
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("rebuilt pack does not match the delta target digest")  # noqa: TRY003
    return target


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": _encode_items(items)}
    return "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object, field: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{field} must be a list of Base64 strings")  # noqa: TRY003
    decoded = []
    for item in value:
        if not isinstance(item, str):
            raise ContextPackError(f"{field} must contain Base64 strings")  # noqa: TRY003
        try:
            decoded.append(base64.b64decode(item.encode("ascii"), validate=True))
        except (binascii.Error, UnicodeEncodeError, ValueError) as error:
            raise ContextPackError(f"{field} contains invalid Base64 data") from error  # noqa: TRY003
    return tuple(decoded)


def _load_object(data: bytes) -> dict[str, object]:
    if not isinstance(data, bytes):
        raise ContextPackError("wire payload must be bytes")  # noqa: TRY003
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("wire payload must be UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict):
        raise ContextPackError("wire payload must be a JSON object")  # noqa: TRY003
    return payload


def _require_fields(payload: dict[str, object], expected: frozenset[str], kind: str) -> None:
    if set(payload) != expected:
        raise ContextPackError(f"{kind} must contain exactly {sorted(expected)}")  # noqa: TRY003


def _require_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ContextPackError(f"{field} must be a sha256 digest")  # noqa: TRY003
    try:
        int(value.removeprefix("sha256:"), 16)
    except ValueError as error:
        raise ContextPackError(f"{field} must be lowercase hexadecimal") from error  # noqa: TRY003
    if value != value.lower():
        raise ContextPackError(f"{field} must be lowercase hexadecimal")  # noqa: TRY003
    return value


def _as_bytes(item: object) -> bytes:
    if isinstance(item, bytes):
        return item
    if isinstance(item, bytearray | memoryview):
        return bytes(item)
    raise ContextPackError("pack items must be bytes")  # noqa: TRY003


def _as_text(content: object) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("content must be valid UTF-8") from error  # noqa: TRY003
    else:
        raise ContextPackError("content must be str or bytes")  # noqa: TRY003
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ContextPackError("content must be valid UTF-8") from error  # noqa: TRY003
    return text


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
