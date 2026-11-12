"""Run the cross-check for one application: extract, merge, compare, enforce grounding."""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from crosscheck.applications import Application
from crosscheck.compare import Flag, compare, ungrounded_flags
from crosscheck.corpus import LoadedDocument
from crosscheck.facts import ExtractionResult, PatientFacts, merge_results
from crosscheck.pharmacy import PharmacyRow

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
    results = [extractor.extract(doc.doc_id, doc.text) for doc in docs]
    facts = merge_results(results)
    flags = compare(app, facts, pharmacy_rows)
    dropped = ungrounded_flags(flags, {d.doc_id: d.text for d in docs}, pharmacy_rows)
    kept = [f for f in flags if f not in dropped]
    return CheckResult(app.app_id, kept, facts, dropped, results)
