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
from dataclasses import dataclass
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

# Retrieval walks a small set of probes over the lexical index and then reranks every
# distinct page it saw. The probes cover the question itself, its domain synonyms, and
# the standard report sections a question of that shape is answered in; reranking scores
# question-term coverage, proximity, and that section prior. Every returned citation is
# read back through the public Memory API, so a citation never names a page it did not see.
_PROBE_LIMIT: Final = 40
_MAX_PROBES: Final = 16
_MAX_CANDIDATES: Final = 240
_PROXIMITY_WINDOW: Final = 40
_FREQUENCY_CAP: Final = 0.4
_FOCUS_VOTES: Final = 40
_FOCUS_BONUS: Final = 3.0
_NO_STATEMENT: Final = 1_000

_QUESTION_FUNCTION_WORDS: Final = frozenset(
    """
    a an the and or for of to in on at by from with as is are was were be been being
    what which who how why when where do does did can could should would i we you it its
    our your this that these those please give give me state stated out outlined directly
    many much any all as of if then than there their they them has have had also may might
    """.split()
)
_TERM_PATTERN: Final = re.compile(r"[a-z]+|\d+")
_NOTE_PATTERN: Final = re.compile(r"\bnote\s+\d+\b")
# FinanceBench appends "if X is not a useful metric, state that and explain why" to some
# questions. Those words describe the expected answer, not the page that carries it.
_BOILERPLATE: Final = re.compile(
    r"\s*(?:if|when)\s+(?:the\s+)?[^.?]*?\b(?:is|are)\s+not\s+(?:a\s+)?"
    r"(?:relevant|useful|applicable|meaningful)[^.?!]*[.?!]?"
    r"|\.?\s*please\s+state[^.?!]*[.?!]?"
    r"|\.?\s*(?:and\s+)?(?:then\s+)?explain\s+why[^.?!]*[.?!]?",
    re.IGNORECASE,
)

_SYNONYMS: Final = {
    "revenue": ("sales", "net sales", "total revenues"),
    "sales": ("revenue",),
    "income": ("earnings", "profit"),
    "profit": ("income", "earnings"),
    "margin": ("gross profit", "operating income"),
    "tax": ("provision", "income taxes"),
    "cash": ("cash flows", "liquidity"),
    "liquidity": ("cash", "current assets", "current liabilities", "working capital"),
    "ratio": ("current assets", "current liabilities"),
    "geography": ("geographic", "emea", "apac", "international", "region"),
    "geographies": ("geographic", "emea", "apac", "international", "region"),
    "segment": ("segments", "reportable", "business segment"),
    "customer": ("customers", "concentration"),
    "concentration": ("credit risk", "customer"),
    "legal": ("litigation", "proceedings", "contingencies"),
    "product": ("products", "services"),
    "category": ("segment", "product"),
    "categories": ("segment", "product"),
    "ebitda": ("operating income", "depreciation", "amortization"),
    "capex": ("capital expenditures",),
    "cyclical": ("cyclical", "cyclicality", "seasonality"),
    "retain": ("retention", "retained"),
    "debt": ("borrowings", "long-term debt", "notes"),
    "repurchase": ("buyback", "share repurchase"),
    "dividend": ("dividends",),
}

_STATEMENTS: Final = (
    ("balance", ("consolidated balance sheet",)),
    ("income", (
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated statements of operations",
        "consolidated statement of operations",
    )),
    ("cashflow", ("consolidated statement of cash flows", "consolidated statements of cash flows")),
    ("equity", (
        "consolidated statements of stockholders",
        "consolidated statements of changes in equity",
        "consolidated statement of equity",
    )),
)
_METRIC_INTENTS: Final = frozenset(
    {"liquidity", "balance", "margin", "tax", "cashflow", "ebitda", "debt", "segment"}
)
_QUALITATIVE_INTENTS: Final = frozenset(
    {"geography", "legal", "customer", "product", "employees", "cyclical", "production"}
)

_INTENT_RULES: Final = (
    ("liquidity", ("liquidity", "quick ratio", "current ratio", "working capital")),
    ("balance", ("balance sheet", "liabilit", "total assets", "shareholders' equity")),
    ("geography", ("geograph", "country", "countries", "region", "operate in", "operations in")),
    ("legal", ("legal", "litigation", "lawsuit", "battle", "proceeding", "contingenc")),
    ("customer", ("customer", "client")),
    ("product", ("product", "service", "offering")),
    ("segment", ("segment",)),
    ("tax", ("tax rate", "effective tax", "income tax")),
    ("margin", ("margin",)),
    ("cashflow", ("cash flow", "operating activities", "investing", "financing")),
    ("employees", ("employee", "headcount", "workforce")),
    ("cyclical", ("cyclical",)),
    ("production", ("production rate", "build rate")),
    ("debt", ("debt", "borrow", "leverage")),
    ("restructuring", ("restructur",)),
    ("dividend", ("dividend", "repurchase", "buyback")),
    ("ebitda", ("ebitda", "depreciation", "amortization")),
    ("revmix", ("categor", "disaggregat")),
)
_INTENT_PROBES: Final = {
    "liquidity": ("liquidity", "current assets", "current liabilities", "working capital"),
    "geography": ("geographic", "international", "emea", "apac", "north america"),
    "legal": ("legal proceedings", "litigation", "contingencies", "claims"),
    "customer": ("customer concentration", "significant customers", "no single customer", "customers"),
    "product": ("products and services", "our products", "product categories", "sales of products"),
    "segment": ("reportable segments", "business segment", "results of operations", "segment data"),
    "tax": ("effective tax rate", "income taxes", "provision for income taxes", "tax expense"),
    "margin": ("gross margin", "gross profit", "operating income", "operating margin"),
    "cashflow": ("operating activities", "investing activities", "financing activities", "cash flows"),
    "employees": ("employees", "human capital"),
    "cyclical": ("cyclical", "seasonality"),
    "production": ("production rate", "build rate", "deliveries"),
    "debt": ("long-term debt", "borrowings", "notes payable"),
    "restructuring": ("restructuring",),
    "dividend": ("dividends", "share repurchase"),
    "ebitda": ("ebitda", "depreciation and amortization", "operating income"),
    "revmix": ("business segment data", "segment data", "disaggregation of revenue", "revenues by", "sales of products"),
}
_STATEMENT_PROBES: Final = (
    "consolidated statements of operations year ended december 31",
    "consolidated statements of income year ended december 31",
    "consolidated balance sheets december 31",
    "consolidated statements of cash flows year ended december 31",
)


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


@dataclass(frozen=True)
class _Candidate:
    """One distinct evidence page reachable from the probes, with its reranking features."""

    citation: MemoryCitation
    document: str
    sequence: tuple[str, ...]
    counts: dict[str, int]
    head: str
    text: str
    statement: str
    statement_offset: int
    is_note: bool


async def search_pdf_pages(
    memory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact citations for the best matching, distinct evidence pages."""

    if limit <= 0 or not isinstance(question, str) or not question.strip():
        return ()
    # Imported here because the built-in Runtime imports this module while it loads.
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    intents = _intents(question)
    query = _query_terms(question)
    if not query:
        return ()

    candidates: dict[tuple[str, str, str], _Candidate] = {}
    for probe in _probes(question, query, intents):
        page = await memory.search(SearchMemoryRequest(query=probe, mode="fts", limit=_PROBE_LIMIT))
        for hit in page.hits:
            key = (str(hit.memory_ref), hit.entry_id, hit.entry_version_id)
            if key in candidates:
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            candidates[key] = _candidate(citation, hit.text)
            if len(candidates) >= _MAX_CANDIDATES:
                break
        if len(candidates) >= _MAX_CANDIDATES:
            break
    if not candidates:
        return ()

    ordered = _rank(tuple(candidates.values()), query, intents)
    # Only the pages handed to the caller are read back, so every returned citation names a
    # page this search actually verified rather than one it merely scored.
    selected: list[MemoryCitation] = []
    seen: set[str] = set()
    for candidate in ordered:
        if len(selected) >= limit:
            break
        entry = (await memory.get(GetMemoryEntryRequest(citation=candidate.citation))).entry
        location = _page_source_id(entry)
        if location is None or location in seen:
            continue
        seen.add(location)
        selected.append(candidate.citation)
    return tuple(selected)


def _rank(candidates: tuple[_Candidate, ...], query: tuple[str, ...], intents: frozenset[str]) -> list[_Candidate]:
    """Order candidates by lexical coverage, proximity, and the section a question implies."""

    total = len(candidates)
    document_frequency: dict[str, int] = {}
    for candidate in candidates:
        for term in candidate.counts:
            document_frequency[term] = document_frequency.get(term, 0) + 1
    # These statistics come from the probed candidates, not the whole collection, and the
    # probes are biased toward pages that already share the question's vocabulary. Capping
    # the frequency keeps a term the probes over-sampled from losing all of its weight.
    ceiling = max(1.0, total * _FREQUENCY_CAP)
    inverse = {
        term: math.log(1 + (total - min(frequency, ceiling) + 0.5) / (min(frequency, ceiling) + 0.5))
        for term, frequency in document_frequency.items()
    }
    average = sum(len(candidate.sequence) for candidate in candidates) / max(total, 1)
    expanded = _expanded_terms(query)
    rare = tuple(term for term in expanded if inverse.get(term, 0.0) >= 1.6)
    scored = [
        (_score(candidate, expanded, rare, inverse, average, intents), candidate)
        for candidate in candidates
    ]
    scored.sort(key=lambda item: item[0], reverse=True)

    # One scope can hold several reports. The question names or describes exactly one of
    # them, so that report's pages get a bonus instead of competing on equal terms with every
    # other report in the scope.
    focus = _focus_document(scored, query)
    if focus is not None:
        scored = [
            (value + _FOCUS_BONUS if candidate.document == focus else value, candidate)
            for value, candidate in scored
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [candidate for _score_value, candidate in scored]

    # Metric questions are answered by a primary statement often enough that its pages
    # stay visible even when a narrative page repeats more of the question's wording.
    if intents & _METRIC_INTENTS:
        reserved: list[_Candidate] = []
        for statement in ("income", "balance", "cashflow"):
            # A statement page opens with its title; a mention further down is prose.
            best = min(
                (item for item in ranked if item.statement == statement and (focus is None or item.document == focus)),
                key=lambda item: (item.statement_offset, ranked.index(item)),
                default=None,
            )
            if best is not None:
                reserved.append(best)
        if reserved:
            ranked = reserved + [item for item in ranked if item not in reserved]
    return ranked


def _document_of(text: str) -> str:
    """Return the document identifier that every captured page text starts with."""

    return text.split("\n", 1)[0].strip().casefold()


def _focus_document(scored: list[tuple[float, _Candidate]], query: tuple[str, ...]) -> str | None:
    """Pick the report a question is about, when the scope holds more than one."""

    documents = {candidate.document for _value, candidate in scored}
    if len(documents) < 2:
        return None
    # A report whose identifier carries a distinctive question term ("boeing", "pepsico",
    # "amd") answers the question itself; the rest of the question only confirms the page.
    named = [
        document
        for document in documents
        if any(term in document for term in query if len(term) > 2 and not term.isdigit())
    ]
    if len(named) == 1:
        return named[0]
    votes: dict[str, int] = {}
    for _value, candidate in scored[:_FOCUS_VOTES]:
        votes[candidate.document] = votes.get(candidate.document, 0) + 1
    return max(votes, key=lambda name: votes[name])


def _score(
    candidate: _Candidate,
    query: tuple[str, ...],
    rare: tuple[str, ...],
    inverse: dict[str, float],
    average: float,
    intents: frozenset[str],
) -> float:
    length = len(candidate.sequence)
    unique = tuple(dict.fromkeys(query))
    # BM25 (k1=1.2, b=0.75), then IDF-weighted query coverage, then how tightly the rarest
    # question terms cluster on the page.
    value = 0.0
    for term in unique:
        frequency = candidate.counts.get(term, 0)
        if not frequency:
            continue
        value += inverse.get(term, 0.0) * frequency * 2.2 / (
            frequency + 1.2 * (0.25 + 0.75 * length / max(average, 1))
        )
    weights = [inverse.get(term, 0.3) for term in unique]
    denominator = sum(weights) or 1.0
    covered = sum(weight for term, weight in zip(unique, weights, strict=True) if term in candidate.counts)
    value += 3.0 * covered / denominator
    value += 2.0 * _proximity(candidate.sequence, rare)

    if candidate.statement:
        if intents & _METRIC_INTENTS and candidate.statement in {"balance", "income", "cashflow"}:
            value += 2.0
        elif intents & _QUALITATIVE_INTENTS:
            value -= 0.5
    if candidate.is_note and intents & _QUALITATIVE_INTENTS:
        value -= 1.2
    if "revmix" in intents:
        revenue = candidate.counts.get("revenu", 0) + candidate.counts.get("sale", 0)
        if length < 400 and revenue >= 3:
            value += 1.6
        if candidate.counts.get("segment", 0) and candidate.counts.get("revenu", 0):
            value += 1.2
    for intent in intents:
        for probe in _INTENT_PROBES.get(intent, ()):
            if probe in candidate.head:
                value += 1.0
            elif probe in candidate.text:
                value += 0.3
    return value


def _candidate(citation: MemoryCitation, text: str) -> _Candidate:
    flat = " ".join(text.split())
    document = _document_of(text)
    head = flat[:300].lower()
    lowered = flat[:700].lower()
    # A statement page *opens* with its title. A note that merely cites "the Consolidated
    # Statements of Income", or the index that lists it, must not be mistaken for it.
    body = text.split("\n", 1)[1] if "\n" in text else text
    body = " ".join(body.split()).lower().removeprefix("table of contents").strip()
    statement, offset = "", _NO_STATEMENT
    if not body.startswith(("note ", "item ", "index to", "part ")):
        for name, markers in _STATEMENTS:
            for marker in markers:
                found = body[:160].find(marker)
                if 0 <= found < offset:
                    statement, offset = name, found
    sequence = tuple(_normalize(term) for term in _TERM_PATTERN.findall(text.casefold()))
    counts: dict[str, int] = {}
    for term in sequence:
        counts[term] = counts.get(term, 0) + 1
    return _Candidate(
        citation=citation,
        document=document,
        sequence=sequence,
        counts=counts,
        head=head,
        text=flat.casefold(),
        statement=statement,
        statement_offset=offset,
        is_note=bool("notes to the consolidated financial statements" in lowered or _NOTE_PATTERN.search(lowered)),
    )


def _proximity(sequence: tuple[str, ...], rare: tuple[str, ...]) -> float:
    """Best concentration of distinct rare question terms inside one page window."""

    if not rare:
        return 0.0
    wanted = frozenset(rare)
    seen: dict[str, int] = {}
    best = 0
    for index, token in enumerate(sequence):
        if token in wanted:
            seen[token] = seen.get(token, 0) + 1
        if index >= _PROXIMITY_WINDOW:
            old = sequence[index - _PROXIMITY_WINDOW]
            if old in wanted:
                seen[old] -= 1
                if not seen[old]:
                    del seen[old]
        if len(seen) > best:
            best = len(seen)
    return best / len(wanted)


def _probes(question: str, query: tuple[str, ...], intents: frozenset[str]) -> list[str]:
    """Build lexical probes that together cover the question and its likely sections."""

    probes: list[str] = []

    def add(value: str) -> None:
        value = " ".join(value.split())
        if value and value not in probes:
            probes.append(value)

    add(question)
    add(_strip_boilerplate(question))
    add(" ".join(query))
    expanded = _expanded_terms(query)
    if expanded:
        add(" ".join(expanded))
    # Statement probes run before the section probes: a metric question's answer page is
    # often one of these three, and the probe budget is finite.
    if intents & _METRIC_INTENTS:
        for probe in _STATEMENT_PROBES:
            add(probe)
    for intent in sorted(intents):
        for probe in _INTENT_PROBES.get(intent, ())[:4]:
            add(probe)
    # Rare-term pairs keep recall when the whole question is too long for the admission
    # floor; the reranker still decides the order.
    for first, second in zip(query[::2], query[1::2], strict=False):
        add(f"{first} {second}")
    return probes[:_MAX_PROBES]


def _intents(question: str) -> frozenset[str]:
    lowered = question.casefold()
    return frozenset(name for name, needles in _INTENT_RULES if any(needle in lowered for needle in needles))


def _query_terms(question: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            _normalize(term)
            for term in _TERM_PATTERN.findall(_strip_boilerplate(question).casefold())
            if term not in _QUESTION_FUNCTION_WORDS and (len(term) > 1 or term.isdigit())
        )
    )


def _expanded_terms(query: tuple[str, ...]) -> tuple[str, ...]:
    expanded: list[str] = []
    for term in query:
        expanded.append(term)
        for synonym in _SYNONYMS.get(term, ()):
            expanded.extend(_normalize(token) for token in _TERM_PATTERN.findall(synonym.casefold()))
    return tuple(dict.fromkeys(expanded))


def _strip_boilerplate(question: str) -> str:
    cleaned = _BOILERPLATE.sub(" ", question)
    return " ".join(cleaned.split()) or question


def _normalize(term: str) -> str:
    """Collapse plurals and silent-e so ``revenue``/``revenues`` meet in the index."""

    if len(term) > 4 and term.endswith("ies"):
        term = term[:-3] + "y"
    elif len(term) > 4 and term.endswith("ses"):
        term = term[:-2]
    elif len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        term = term[:-1]
    if len(term) > 3 and term.endswith("e"):
        term = term[:-1]
    return term


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
