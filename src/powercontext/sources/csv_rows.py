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

"""One captured Source per CSV record, addressed by document and primary key."""

from __future__ import annotations

import csv
import io
import json
from typing import Final
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME: Final = "csv-row"
CSV_ROW_SOURCE_VERSION: Final = "1"
CSV_ROW_IDENTITY_SCHEME: Final = "csv"
_DELIMITER: Final = ","
_QUOTE_CHAR: Final = '"'
_UNSAFE: Final = ""


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document and the record key that identifies rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One parsed CSV record with its header order and raw field values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_width(self) -> CSVRowInput:
        _require_matching_width(self.columns, self.values)
        return self


class CSVRowSource(Source):
    """Captured evidence for exactly one CSV record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_width(self) -> CSVRowSource:
        _require_matching_width(self.columns, self.values)
        return self


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate a whole CSV document before returning any row."""

    document_id = _validate_document_id(value.document_id)
    primary_key = _validate_primary_key(value.primary_key)
    reader = csv.reader(
        io.StringIO(_decode(value.content), newline=""),
        delimiter=_DELIMITER,
        quotechar=_QUOTE_CHAR,
        doublequote=True,
        strict=True,
    )
    try:
        header = next(reader, None)
        if header is None:
            raise ValueError("CSV document must contain a header record")  # noqa: TRY003
        columns = _validate_header(header)
        try:
            key_index = columns.index(primary_key)
        except ValueError:
            raise ValueError(f"primary_key {primary_key!r} must match one header column exactly") from None

        rows: list[CSVRowInput] = []
        seen: set[str] = set()
        for record in reader:
            if not record:
                # A completely empty physical record carries no field values.
                continue
            if len(record) != len(columns):
                raise ValueError(  # noqa: TRY003
                    f"CSV record has {len(record)} fields but the header declares {len(columns)}"
                )
            primary_key_value = record[key_index]
            if not primary_key_value.strip():
                raise ValueError("CSV primary key value must not be empty or blank")  # noqa: TRY003
            if primary_key_value in seen:
                raise ValueError(f"CSV document repeats primary key value {primary_key_value!r}")  # noqa: TRY003
            seen.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=document_id,
                    primary_key=primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError(f"malformed CSV document: {error}") from error
    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the delimiter-safe identity of one CSV row Source."""

    document = _validate_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-empty, non-blank string")  # noqa: TRY003
    identity = (
        f"{CSV_ROW_IDENTITY_SCHEME}:{quote(document, safe=_UNSAFE)}:"
        f"{quote(primary_key_value, safe=_UNSAFE)}"
    )
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"csv row Source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )
    return identity


class CSVRowAdapter:
    """Resolve and read the row-level CSV Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput) -> CSVRowSource:
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            source_id=identity,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose the complete record as canonical textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSVRowAdapter(),
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)


def _require_matching_width(columns: tuple[str, ...], values: tuple[str, ...]) -> None:
    if len(columns) != len(values):
        raise ValueError("columns and values must have the same length")  # noqa: TRY003


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be strict UTF-8 with an optional leading BOM") from error


def _validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str) or not document_id:
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return document_id


def _validate_primary_key(primary_key: str) -> str:
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    return primary_key


def _validate_header(header: list[str]) -> tuple[str, ...]:
    if not header:
        raise ValueError("CSV header must not be empty")  # noqa: TRY003
    columns = tuple(header)
    seen: set[str] = set()
    for column in columns:
        if not column or not column.strip():
            raise ValueError("CSV header columns must be non-empty and not blank")  # noqa: TRY003
        if column in seen:
            raise ValueError(f"CSV header repeats column {column!r}")  # noqa: TRY003
        seen.add(column)
    return columns


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowAdapter",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
