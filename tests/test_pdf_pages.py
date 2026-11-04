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
import hashlib
import io

import pytest
from pypdf import PdfWriter

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    TextEvidence,
    manifest_for_definition,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    PDFPageSource,
    parse_pdf_document,
    pdf_page_source_id,
)


def _blank_pdf(pages: int = 2, **metadata: str) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=200)
    if metadata:
        writer.add_metadata(metadata)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_parse_returns_every_physical_page_in_order() -> None:
    content = _blank_pdf(3)

    pages = parse_pdf_document(PDFDocumentInput(document_id="report", content=content))

    assert isinstance(pages, tuple)
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert [page.text for page in pages] == ["", "", ""]
    assert {page.document_sha256 for page in pages} == {hashlib.sha256(content).hexdigest()}
    assert {page.document_id for page in pages} == {"report"}


def test_parse_rejects_blank_document_id() -> None:
    for document_id in ("", "   ", " report "):
        with pytest.raises(ValueError):
            parse_pdf_document(PDFDocumentInput(document_id=document_id, content=_blank_pdf(1)))


@pytest.mark.parametrize("raw", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_corrupt_documents_fail_atomically(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="invalid", content=raw))


def test_empty_password_permission_marker_is_readable() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="owner")
    buffer = io.BytesIO()
    writer.write(buffer)

    pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=buffer.getvalue()))

    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


def test_identity_is_stable_and_revision_sensitive() -> None:
    original = _blank_pdf(1)
    updated = _blank_pdf(1, **{"/Title": "New revision"})

    assert pdf_page_source_id("control", hashlib.sha256(original).hexdigest(), 1) == pdf_page_source_id(
        "control", hashlib.sha256(original).hexdigest(), 1
    )
    assert pdf_page_source_id("control", hashlib.sha256(original).hexdigest(), 1) != pdf_page_source_id(
        "control", hashlib.sha256(updated).hexdigest(), 1
    )
    with pytest.raises(ValueError):
        pdf_page_source_id("control", "not-a-digest", 1)
    with pytest.raises(ValueError):
        pdf_page_source_id("control", hashlib.sha256(original).hexdigest(), 0)


def test_definition_reads_and_projects_the_page() -> None:
    digest = hashlib.sha256(b"content").hexdigest()
    page = PDFPageInput(document_id="report", document_sha256=digest, page_number=7, text="Net revenue 86,392")
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))

    source = asyncio.run(registry.resolve(page))

    assert isinstance(source, PDFPageSource)
    assert source.name == source.source_id == pdf_page_source_id("report", digest, 7)
    assert source.materialization.value == "captured"
    assert asyncio.run(registry.read(source)) == page
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence == TextEvidence(
        source_type="pdf-page",
        source_id=source.name,
        content="report\nNet revenue 86,392",
        metadata={"document_id": "report", "document_sha256": digest, "page_number": 7},
    )
    assert manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION).name == "pdf-page"
