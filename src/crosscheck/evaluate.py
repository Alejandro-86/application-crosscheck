"""Evaluation: how good is extraction, and how good are the resulting flags?

Two levels are scored separately, because they fail differently:
  1. Extraction: facts found in each note vs what the generator put there (per fact kind).
  2. Flags: flags produced from extracted facts vs the flags a perfect extractor would produce.
The deterministic comparison rules are identical in both, so any gap comes from extraction.

Command line:
  python -m crosscheck.evaluate --extractor rules --out docs/results-rules.md
  python -m crosscheck.evaluate --extractor llm --limit 20 --out docs/results-llm.md
"""

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from crosscheck.compare import SEVERITY, compare
from crosscheck.corpus import (
    LoadedDocument,
    load_applications,
    load_documents,
    load_patients,
    load_pharmacy,
)
from crosscheck.oracle import doc_truth, oracle_facts
from crosscheck.pipeline import Extractor, check_application, index_documents


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return (
            2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
        )

    def add(self, predicted: set, expected: set) -> None:
        self.tp += len(predicted & expected)
        self.fp += len(predicted - expected)
        self.fn += len(expected - predicted)


@dataclass
class EvalReport:
    extractor: str
    n_applications: int
    n_documents: int
    planted_errors: int
    extraction: dict[str, PRF] = field(default_factory=dict)
    flags: dict[str, PRF] = field(default_factory=dict)
    overall: PRF = field(default_factory=PRF)
    dropped_flags: int = 0
    ungrounded_quotes: int = 0
    unmapped_terms: int = 0
    retried_documents: int = 0
    false_positives: list[tuple[str, tuple[str, str]]] = field(default_factory=list)
    false_negatives: list[tuple[str, tuple[str, str]]] = field(default_factory=list)


def run_evaluation(
    extractor: Extractor, name: str, data_dir: Path, limit: int | None = None
) -> EvalReport:
    patients = {p.patient_id: p for p in load_patients(data_dir)}
    applications = load_applications(data_dir)[:limit]
    by_mrn = index_documents(load_documents(data_dir))
    pharmacy: dict[str, list] = defaultdict(list)
    for row in load_pharmacy(data_dir):
        pharmacy[row.mrn].append(row)

    docs_used = [d for app in applications for d in by_mrn[app.mrn]]
    report = EvalReport(
        extractor=name,
        n_applications=len(applications),
        n_documents=len(docs_used),
        planted_errors=sum(len(a.planted_errors) for a in applications),
        extraction={k: PRF() for k in ("condition", "negated_condition", "medication", "allergy")},
        flags={t: PRF() for t in SEVERITY},
    )

    for app in applications:
        patient = patients[app.patient_id]
        docs = by_mrn[app.mrn]
        result = check_application(app, docs, pharmacy[app.mrn], extractor)
        _score_extraction(report, patient, docs, result.results)
        report.dropped_flags += len(result.dropped)

        expected = {f.key for f in compare(app, oracle_facts(patient, docs), pharmacy[app.mrn])}
        predicted = {f.key for f in result.flags}
        report.overall.add(predicted, expected)
        for flag_type, prf in report.flags.items():
            prf.add(
                {k for k in predicted if k[0] == flag_type},
                {k for k in expected if k[0] == flag_type},
            )
        report.false_positives += [(app.app_id, k) for k in sorted(predicted - expected)]
        report.false_negatives += [(app.app_id, k) for k in sorted(expected - predicted)]
    return report


def _score_extraction(report: EvalReport, patient, docs: list[LoadedDocument], results) -> None:
    by_doc = {r.doc_id: r for r in results}
    for doc in docs:
        result = by_doc[doc.doc_id]
        report.ungrounded_quotes += len(result.ungrounded)
        report.unmapped_terms += len(result.unmapped)
        report.retried_documents += result.attempts > 1
        truth = doc_truth(patient, doc.fields_present)
        got = {(f.kind, f.value, f.dose) for f in result.facts}
        for kind, prf in report.extraction.items():
            prf.add({g for g in got if g[0] == kind}, {t for t in truth if t[0] == kind})


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def format_report(report: EvalReport) -> str:
    lines = [
        f"# Results: {report.extractor} extractor",
        "",
        "Synthetic data only. The comparison rules are deterministic; any gap below comes from "
        "extraction. The rule-based extractor works because the synthetic notes use a few "
        "templates, so strong numbers there say little about real notes.",
        "",
        f"- Applications: {report.n_applications} ({report.planted_errors} planted errors)",
        f"- Notes processed: {report.n_documents}",
        f"- Flags dropped for missing evidence: {report.dropped_flags}",
        f"- Quotes not found in the note (dropped): {report.ungrounded_quotes}",
        f"- Terms with no canonical mapping: {report.unmapped_terms}",
        f"- Notes needing a retry: {report.retried_documents}",
        "",
        "## Extraction (per fact kind, scored against the generator's labels)",
        "",
        "| Kind | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|",
    ]
    for kind, prf in report.extraction.items():
        lines.append(
            f"| {kind} | {prf.tp} | {prf.fp} | {prf.fn} | {_fmt(prf.precision)} | "
            f"{_fmt(prf.recall)} | {_fmt(prf.f1)} |"
        )
    lines += [
        "",
        "## Flags (scored against the flags a perfect extractor would produce)",
        "",
        "| Flag type | Severity | TP | FP | FN | Precision | Recall | F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for flag_type, prf in report.flags.items():
        lines.append(
            f"| {flag_type} | {SEVERITY[flag_type]} | {prf.tp} | {prf.fp} | {prf.fn} | "
            f"{_fmt(prf.precision)} | {_fmt(prf.recall)} | {_fmt(prf.f1)} |"
        )
    o = report.overall
    lines += [
        f"| **overall** |  | {o.tp} | {o.fp} | {o.fn} | {_fmt(o.precision)} | {_fmt(o.recall)} | "
        f"{_fmt(o.f1)} |",
        "",
        "## Examples of errors",
        "",
        "False positives (flagged, but a perfect extractor would not):",
    ]
    lines += [f"- {a}: {k[0]} / {k[1]}" for a, k in report.false_positives[:10]] or ["- none"]
    lines += ["", "False negatives (a perfect extractor would flag, but this run did not):"]
    lines += [f"- {a}: {k[0]} / {k[1]}" for a, k in report.false_negatives[:10]] or ["- none"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate extraction and flags.")
    parser.add_argument("--extractor", choices=["rules", "llm"], default="rules")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--limit", type=int, default=None, help="only the first N applications")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.extractor == "rules":
        from crosscheck.rules_extractor import RuleExtractor

        extractor: Extractor = RuleExtractor()
    else:
        from crosscheck.config import get_settings
        from crosscheck.llm import ChatClient
        from crosscheck.llm_extractor import LLMExtractor

        settings = get_settings()
        extractor = LLMExtractor(ChatClient(settings))

    report = run_evaluation(extractor, args.extractor, args.data, args.limit)
    text = format_report(report)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    if args.extractor == "llm":
        from crosscheck.costs import UsageLog

        spent = UsageLog(settings.usage_path, settings.max_spend_usd).total_spend()
        print(f"Logged spend so far: ${spent:.4f} (see runs/usage.jsonl)")


if __name__ == "__main__":
    main()
