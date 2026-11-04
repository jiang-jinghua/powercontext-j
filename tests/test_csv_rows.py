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
    SourceDefinitionRegistry,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)


def _document(content: bytes, *, document_id: str = "airports", primary_key: str = "id") -> CSVDocumentInput:
    return CSVDocumentInput(document_id=document_id, primary_key=primary_key, content=content)


def test_parse_preserves_raw_fields_and_skips_empty_records() -> None:
    content = (
        b'\xef\xbb\xbf" id ",name,note\r\n'
        b'\r\n'
        b'001,"Airport, East","first line\r\nsecond ""quoted"" line"\r\n'
        b"\r\n"
    )

    rows = parse_csv_document(_document(content, primary_key=" id "))

    assert len(rows) == 1
    assert rows[0].columns == (" id ", "name", "note")
    assert rows[0].values == ("001", "Airport, East", 'first line\r\nsecond "quoted" line')
    assert rows[0].primary_key_value == "001"


def test_header_only_documents_are_valid_and_empty_documents_are_not() -> None:
    assert parse_csv_document(_document(b"id,name\n\n")) == ()
    with pytest.raises(ValueError):
        parse_csv_document(_document(b""))


@pytest.mark.parametrize(
    "content",
    [
        b"id,name\n1,one\n1,duplicate\n",
        b"id,name\n,empty-key\n",
        b"id,name\n1\n",
        b"id,id\n1,1\n",
        b",name\n1,one\n",
        b" \t,name\n1,one\n",
        b'id,name\n1,"unclosed\n',
        b"id,name\n\xff,invalid utf-8\n",
        b"id,name\n1,x\n,blank\n",
    ],
)
def test_parse_rejects_invalid_documents_without_partial_rows(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(_document(content))


def test_primary_key_must_match_a_header_column_exactly() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(_document(b"id,name\n1,one\n", primary_key="missing"))
    with pytest.raises(ValueError):
        parse_csv_document(_document(b"id,name\n1,one\n", primary_key="ID"))


def test_identity_is_delimiter_safe_and_bounded() -> None:
    assert csv_row_source_id("doc:one", "key/one") == "csv:doc%3Aone:key%2Fone"
    assert csv_row_source_id("doc:one", "key/one") != csv_row_source_id("doc:two", "key/one")
    assert csv_row_source_id("doc:one", "key/one") != csv_row_source_id("doc:one", "key:one")
    assert csv_row_source_id("space-keys", " key ") == "csv:space-keys:%20key%20"

    with pytest.raises(ValueError):
        csv_row_source_id(" doc ", "key")
    with pytest.raises(ValueError):
        csv_row_source_id("doc", " " * 3)
    with pytest.raises(ValueError):
        csv_row_source_id("doc", "x" * 260)


def test_definition_reads_and_projects_the_complete_record() -> None:
    row = parse_csv_document(_document(b'id,name,note\n001,"Airport, East","a ""quote"""\n'))[0]
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))

    source = asyncio.run(registry.resolve(row))

    assert isinstance(source, CSVRowSource)
    assert source.source_id == source.name == "csv:airports:001"
    assert source.materialization.value == "captured"
    assert asyncio.run(registry.read(source)) == row

    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    mapping = {"id": "001", "name": "Airport, East", "note": 'a "quote"'}
    assert evidence.source_type == "csv-row"
    assert evidence.source_id == "csv:airports:001"
    assert evidence.content == json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
    assert evidence.metadata == {
        "document_id": "airports",
        "primary_key": "id",
        "primary_key_value": "001",
        "columns": ["id", "name", "note"],
        "row": mapping,
    }


def test_rows_are_captured_observations_with_a_stable_manifest() -> None:
    rows = parse_csv_document(_document(b"id,name\n01,Alpha\n02,Beta\n"))
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    observations = [project_source_for_transport(registry, asyncio.run(registry.resolve(row))) for row in rows]

    assert [observation.name for observation in observations] == ["csv:airports:01", "csv:airports:02"]
    assert manifest_for_definition(CSV_ROW_SOURCE_DEFINITION) == manifest_for_definition(CSV_ROW_SOURCE_DEFINITION)
    for observation, row in zip(observations, rows, strict=True):
        assert observation.source_type == "csv-row"
        assert observation.definition_version == "1"
        assert observation.payload["columns"] == list(row.columns)
        assert observation.payload["values"] == list(row.values)
        evidence = TextEvidence.model_validate(observation.projection(TEXT_EVIDENCE_PROJECTION_KEY))
        assert evidence.metadata["row"] == dict(zip(row.columns, row.values, strict=True))


def test_row_identity_survives_reordering_and_non_key_edits() -> None:
    original = parse_csv_document(_document(b"id,name\n01,Alpha\n02,Beta\n"))
    updated = parse_csv_document(_document(b"id,name\n02,Beta reordered\n01,Alpha edited\n"))

    assert [row.primary_key_value for row in original] == ["01", "02"]
    assert [csv_row_source_id(row.document_id, row.primary_key_value) for row in original] == [
        csv_row_source_id(row.document_id, row.primary_key_value) for row in reversed(updated)
    ]


def test_row_models_reject_mismatched_width() -> None:
    with pytest.raises(ValueError):
        CSVRowInput(
            document_id="doc",
            primary_key="id",
            primary_key_value="1",
            columns=("id", "name"),
            values=("1",),
        )
