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

"""Capture every physical page of one PDF as addressable evidence."""

from __future__ import annotations

import hashlib
import io
import math
import re
from typing import Final
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, model_validator
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME: Final = "pdf-page"
PDF_PAGE_SOURCE_VERSION: Final = "1"
_IDENTITY_SCHEME: Final = "pdf"
_UNSAFE: Final = ""

# The lexical index supplies a candidate pool; the pages are then reranked by how much
# of the question's rare vocabulary each page actually covers. A second, looser query
# over the leading content terms keeps the pool wide enough for pages that repeat only
# part of a long question. Consulted pages are read through the public Memory API only,
# so every returned citation stays exact.
_QUERY_LIMIT: Final = 50
_CANDIDATE_LIMIT: Final = 80
_RECALL_TERMS: Final = 8

_QUESTION_FUNCTION_WORDS: Final = frozenset(
    """
    a an the and or for of to in on at by from with as is are was were be been being
    what which who how why when where do does did can could should would i we you it its
    our your this that these those please give give me state stated out outlined directly
    many much any all as of if then than there their they them has have had
    """.split()
)
_TERM_PATTERN: Final = re.compile(r"[a-z0-9]+")


class PDFDocumentInput(BaseModel):
    """One complete PDF document and the caller-stable identity it should keep."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical PDF page with its extractable text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_page(self) -> PDFPageInput:
        _validate_digest(self.document_sha256)
        if self.page_number < 1:
            raise ValueError("page_number must start at 1")  # noqa: TRY003
        return self


class PDFPageSource(Source):
    """Captured evidence for exactly one physical PDF page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page, in order, from one complete PDF document."""

    document_id = _validate_document_id(value.document_id)
    content = value.content
    if not content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003
    digest = hashlib.sha256(content).hexdigest()
    texts = _extract_pages(content)
    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=digest,
            page_number=page_number,
            text=text,
        )
        for page_number, text in enumerate(texts, start=1)
    )


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int, /) -> str:
    """Build the delimiter-safe identity of one physical PDF page."""

    document = _validate_document_id(document_id)
    _validate_digest(document_sha256)
    if not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = (
        f"{_IDENTITY_SCHEME}:{quote(document, safe=_UNSAFE)}:{document_sha256}:{page_number}"
    )
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"PDF page Source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )
    return identity


class PDFPageAdapter:
    """Resolve and read the captured PDF page Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            source_id=identity,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one page as ``DOCUMENT_ID`` followed by its extracted text."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    PDFPageAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(
    memory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact citations for the best matching, distinct evidence pages."""

    if limit <= 0 or not isinstance(question, str) or not question.strip():
        return ()
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    terms = tuple(dict.fromkeys(_content_terms(question)))
    if not terms:
        return ()
    queries = [question]
    if len(terms) > _RECALL_TERMS:
        queries.append(" ".join(terms[:_RECALL_TERMS]))

    hits: list[object] = []
    seen: set[tuple[str, str, str]] = set()
    for query in queries:
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=_QUERY_LIMIT))
        for hit in page.hits:
            key = (str(hit.memory_ref), hit.entry_id, hit.entry_version_id)
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
            if len(hits) >= _CANDIDATE_LIMIT:
                break
        if len(hits) >= _CANDIDATE_LIMIT:
            break

    candidates: list[tuple[int, MemoryCitation, str, frozenset[str]]] = []
    for rank, hit in enumerate(hits):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        location = _page_source_id(entry)
        if location is None:
            continue
        candidates.append((rank, citation, location, frozenset(_TERM_PATTERN.findall(entry.text.casefold()))))
    if not candidates:
        return ()

    # Weight query terms by how rare they are inside the retrieved pool: boilerplate
    # words shared by every page cannot outweigh the terms that identify the evidence.
    total = len(candidates)
    frequency = {term: sum(1 for _rank, _citation, _location, words in candidates if term in words) for term in terms}
    weight = {term: math.log(1.0 + total / (1.0 + frequency[term])) for term in terms}
    denominator = sum(weight.values()) or 1.0
    ranked = sorted(
        (
            (sum(weight[term] for term in terms if term in words) / denominator, -rank, citation, location)
            for rank, citation, location, words in candidates
        ),
        reverse=True,
    )

    citations: list[MemoryCitation] = []
    locations: set[str] = set()
    for _coverage, _rank, citation, location in ranked:
        if location in locations:
            continue
        locations.add(location)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)


def _content_terms(question: str) -> list[str]:
    return [
        term
        for term in _TERM_PATTERN.findall(question.casefold())
        if term not in _QUESTION_FUNCTION_WORDS and (len(term) > 1 or term.isdigit())
    ]


def _page_source_id(entry) -> str | None:
    for source in entry.sources:
        if getattr(source, "source_type", None) == PDF_PAGE_SOURCE_NAME:
            return source.source_id
    return None


def _extract_pages(content: bytes) -> tuple[str, ...]:
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF document requires a user password")  # noqa: TRY003
        texts = []
        for page in reader.pages:
            text = page.extract_text()
            texts.append(text if isinstance(text, str) else "")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF document could not be fully parsed") from error
    return tuple(texts)


def _validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty, non-blank string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return document_id


def _validate_digest(document_sha256: str) -> str:
    if not isinstance(document_sha256, str) or len(document_sha256) != 64:
        raise ValueError("document_sha256 must be 64 lowercase hexadecimal characters")  # noqa: TRY003
    if document_sha256.lower() != document_sha256:
        raise ValueError("document_sha256 must be 64 lowercase hexadecimal characters")  # noqa: TRY003
    try:
        int(document_sha256, 16)
    except ValueError:
        raise ValueError("document_sha256 must be 64 lowercase hexadecimal characters") from None  # noqa: TRY003
    return document_sha256


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageAdapter",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
