"""The cross-check for one application. The logic is in `graph.py`; these are its entry points."""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from crosscheck.applications import Application
from crosscheck.compare import Flag
from crosscheck.corpus import LoadedDocument
from crosscheck.embeddings import Embedder
from crosscheck.facts import ExtractionResult, PatientFacts
from crosscheck.pharmacy import PharmacyRow
from crosscheck.retrieval import DEFAULT_K, Index, RetrievalInfo

MRN_PATTERN = re.compile(r"MRN (\d+)")


class Extractor(Protocol):
    def extract(self, doc_id: str, text: str) -> ExtractionResult: ...


@dataclass
class CheckResult:
    app_id: str
    flags: list[Flag]
    facts: PatientFacts
    dropped: list[Flag] = field(default_factory=list)  # discrepancies removed for lacking evidence
    results: list[ExtractionResult] = field(default_factory=list)
    retrieval: RetrievalInfo | None = None


def index_documents(documents: list[LoadedDocument]) -> dict[str, list[LoadedDocument]]:
    by_mrn: dict[str, list[LoadedDocument]] = defaultdict(list)
    for doc in documents:
        match = MRN_PATTERN.search(doc.text)
        if match:
            by_mrn[match[1]].append(doc)
    return by_mrn


def check_application(
    app: Application,
    docs: list[LoadedDocument],
    pharmacy_rows: list[PharmacyRow],
    extractor: Extractor,
) -> CheckResult:
    """Run the check through the LangGraph workflow (batch mode: no pause for review)."""
    from crosscheck.graph import RunContext, run_check

    return run_check(RunContext(app, docs, pharmacy_rows, extractor))


def check_application_rag(
    app: Application,
    docs: list[LoadedDocument],
    pharmacy_rows: list[PharmacyRow],
    extractor: Extractor,
    embedder: Embedder,
    *,
    k: int = DEFAULT_K,
    index: Index | None = None,
) -> CheckResult:
    """Like check_application, but the extractor only reads the retrieved passages."""
    from crosscheck.graph import RunContext, run_check

    return run_check(RunContext(app, docs, pharmacy_rows, extractor, embedder, k, index))
