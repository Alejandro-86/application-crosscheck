"""Evaluation: how good is extraction, and how good are the resulting flags?

Two levels are scored separately, because they fail differently:
  1. Extraction: facts found in each note vs what the generator put there (per fact kind).
  2. Flags: flags produced from extracted facts vs the flags a perfect extractor would produce.
The deterministic comparison rules are identical in both, so any gap comes from extraction.

Applications are read from the store, i.e. as parsed from their raw forms, so a scraper mistake
shows up here too. With --retrieval rag the extractor only reads the retrieved passages, and the
report says how much text that saved and what it cost in missed flags.

Command line:
  python -m crosscheck.evaluate --extractor rules --out docs/results-rules.md
  python -m crosscheck.evaluate --extractor rules --retrieval rag --k 3
  python -m crosscheck.evaluate --sweep --out docs/results-rag.md
  python -m crosscheck.evaluate --extractor llm --limit 20 --out docs/results-llm.md
"""

import argparse
import hashlib
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from crosscheck.compare import SEVERITY, compare
from crosscheck.config import get_settings
from crosscheck.corpus import (
    LoadedDocument,
    load_applications,
    load_documents,
    load_patients,
    load_pharmacy,
)
from crosscheck.embeddings import Embedder, normalise
from crosscheck.facts import ExtractionResult
from crosscheck.oracle import doc_truth, oracle_facts
from crosscheck.pipeline import Extractor, check_application, check_application_rag, index_documents
from crosscheck.retrieval import DEFAULT_K
from crosscheck.store import StoreDB, load_parsed_applications, store_path


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
    retrieval: str = "all text"
    remapped: int = 0
    remapped_examples: list[str] = field(default_factory=list)
    chars_total: int = 0
    chars_sent: int = 0
    by_case: dict[str, PRF] = field(default_factory=dict)
    scraper_exact: int = 0
    scraper_total: int = 0
    false_positives: list[tuple[str, tuple[str, str]]] = field(default_factory=list)
    false_negatives: list[tuple[str, tuple[str, str]]] = field(default_factory=list)


class RandomEmbedder:
    """Control: ranks passages by chance. Retrieval only counts as working if it beats this."""

    name = "random-control"

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            seed = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
            rng = random.Random(seed)
            vectors.append(normalise([rng.uniform(-1, 1) for _ in range(32)]))
        return vectors


def scraper_accuracy(data_dir: Path) -> tuple[int, int]:
    """(forms parsed exactly as the truth file says, total forms in the truth file)."""
    parsed = {a.app_id: a for a in load_parsed_applications(data_dir)}
    exact = 0
    truth = load_applications(data_dir)
    for expected in truth:
        got = parsed.get(expected.app_id)
        exact += got is not None and (
            (got.name, got.mrn, set(got.declared_conditions), set(got.declared_allergies))
            == (
                expected.name,
                expected.mrn,
                set(expected.declared_conditions),
                set(expected.declared_allergies),
            )
            and {(m.name, m.dose) for m in got.declared_medications}
            == {(m.name, m.dose) for m in expected.declared_medications}
        )
    return exact, len(truth)


def run_evaluation(
    extractor: Extractor,
    name: str,
    data_dir: Path,
    limit: int | None = None,
    *,
    embedder: Embedder | None = None,
    k: int = DEFAULT_K,
    use_store_index: bool = True,
) -> EvalReport:
    """With an embedder, the extractor reads only retrieved passages.

    The passage vectors come from the store unless use_store_index is False (then they are
    computed on the fly, which is how the random control runs).
    """
    store = StoreDB(store_path(data_dir))
    if use_store_index and embedder is not None and store.meta().get("embedder") != embedder.name:
        raise ValueError(
            f"The store was embedded with {store.meta().get('embedder')}, not {embedder.name}. "
            "Run python -m crosscheck.ingest with the same embedder."
        )
    patients = {p.mrn: p for p in load_patients(data_dir)}
    applications = load_parsed_applications(data_dir)[:limit]
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
        retrieval="all text" if embedder is None else f"top-{k} per query ({embedder.name})",
    )
    report.scraper_exact, report.scraper_total = scraper_accuracy(data_dir)

    for app in applications:
        patient = patients[app.mrn]
        docs = by_mrn[app.mrn]
        if embedder is None:
            result = check_application(app, docs, pharmacy[app.mrn], extractor)
        else:
            result = check_application_rag(
                app,
                docs,
                pharmacy[app.mrn],
                extractor,
                embedder,
                k=k,
                index=store.index_for_mrn(app.mrn) if use_store_index else None,
            )
        total = sum(len(d.text) for d in docs)
        report.chars_total += total
        report.chars_sent += result.retrieval.chars_sent if result.retrieval else total
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
        result = by_doc.get(doc.doc_id) or ExtractionResult(doc.doc_id)
        report.ungrounded_quotes += len(result.ungrounded)
        report.unmapped_terms += len(result.unmapped)
        report.remapped += len(result.remapped)
        report.remapped_examples += result.remapped[: max(0, 8 - len(report.remapped_examples))]
        report.retried_documents += result.attempts > 1
        truth = doc_truth(patient, doc.fields_present)
        got = {(f.kind, f.value, f.dose) for f in result.facts}
        for kind, prf in report.extraction.items():
            prf.add({g for g in got if g[0] == kind}, {t for t in truth if t[0] == kind})
        for case in doc.hard_cases or ["(none)"]:
            report.by_case.setdefault(case, PRF()).add(got, truth)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _pct(part: int, whole: int) -> str:
    return "n/a" if not whole else f"{100 * part / whole:.0f}%"


def format_sweep(reports: list[EvalReport]) -> str:
    lines = [
        "# Results: reading everything vs retrieval (rules extractor)",
        "",
        "Synthetic data only. The same rules extractor and the same comparison rules run in every "
        "row; only the text it is given changes. 'Text sent' is the share of note characters "
        "passed to the extractor (a proxy for model tokens). The random-control rows rank passages "
        "by chance, so they show what retrieval has to beat. The topic queries and the notes' "
        "line structure are friendly to this retrieval, and every note of a patient repeats the "
        "same facts, so treat the recall as an upper bound. If the data includes narrative "
        "letters, this rules extractor cannot read them with or without retrieval, so any "
        "retrieval loss on them is hidden: run `--extractor llm --retrieval rag` to see it.",
        "",
        "| Extractor input | Text sent | Flag precision | Flag recall | Flag F1 | Missed flags |",
        "|---|---|---|---|---|---|",
    ]
    for r in reports:
        o = r.overall
        lines.append(
            f"| {r.retrieval} | {_pct(r.chars_sent, r.chars_total)} | {_fmt(o.precision)} | "
            f"{_fmt(o.recall)} | {_fmt(o.f1)} | {o.fn} of {o.tp + o.fn} |"
        )
    return "\n".join(lines) + "\n"


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
        f"- Applications parsed from their raw forms exactly as the truth file says: "
        f"{report.scraper_exact}/{report.scraper_total}",
        f"- Extractor input: {report.retrieval}; "
        f"{report.chars_sent}/{report.chars_total} characters of notes sent "
        f"({_pct(report.chars_sent, report.chars_total)})",
        f"- Flags dropped for missing evidence: {report.dropped_flags}",
        f"- Quotes not found in the note (dropped): {report.ungrounded_quotes}",
        f"- Terms with no canonical mapping (left out of the comparison): {report.unmapped_terms}",
        f"- Terms mapped by the model fallback (closed list, quote kept): {report.remapped}",
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
        "## Extraction by kind of note (a note counts under every hard case it contains)",
        "",
        "| Hard case | TP | FP | FN | Precision | Recall |",
        "|---|---|---|---|---|---|",
    ]
    for case, prf in sorted(report.by_case.items()):
        lines.append(
            f"| {case} | {prf.tp} | {prf.fp} | {prf.fn} | {_fmt(prf.precision)} | "
            f"{_fmt(prf.recall)} |"
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
    if report.remapped_examples:
        lines += ["", "Examples of terms the model fallback mapped:"]
        lines += [f"- {example}" for example in report.remapped_examples]
    lines += ["", "False negatives (a perfect extractor would flag, but this run did not):"]
    lines += [f"- {a}: {k[0]} / {k[1]}" for a, k in report.false_negatives[:10]] or ["- none"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate extraction and flags.")
    parser.add_argument("--extractor", choices=["rules", "llm"], default="rules")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--limit", type=int, default=None, help="only the first N applications")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--retrieval", choices=["all", "rag"], default="all")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--embedder", choices=["hashing", "openai"], default="hashing")
    parser.add_argument("--sweep", action="store_true", help="compare all text with several k")
    parser.add_argument(
        "--mapper",
        choices=["none", "llm"],
        default="none",
        help="llm: ask the model to map terms the table does not know (one call per new term)",
    )
    args = parser.parse_args()

    if not store_path(args.data).exists():
        from crosscheck.embeddings import get_embedder
        from crosscheck.ingest import ingest

        print(ingest(args.data, get_embedder("hashing")).summary())

    if args.sweep:
        from crosscheck.embeddings import get_embedder
        from crosscheck.rules_extractor import RuleExtractor

        embedder = get_embedder("hashing")
        reports = [run_evaluation(RuleExtractor(), "rules", args.data, args.limit)]
        reports += [
            run_evaluation(RuleExtractor(), "rules", args.data, args.limit, embedder=embedder, k=k)
            for k in (1, 2, 3, 5)
        ]
        reports += [
            run_evaluation(
                RuleExtractor(),
                "rules",
                args.data,
                args.limit,
                embedder=RandomEmbedder(),
                k=k,
                use_store_index=False,
            )
            for k in (1, 3)
        ]
        text = format_sweep(reports)
        print(text)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
        return

    embedder = None
    if args.retrieval == "rag":
        from crosscheck.embeddings import get_embedder

        embedder = get_embedder(
            args.embedder, get_settings() if args.embedder == "openai" else None
        )

    if args.extractor == "rules":
        from crosscheck.rules_extractor import RuleExtractor

        extractor: Extractor = RuleExtractor()
    else:
        from crosscheck.llm import ChatClient
        from crosscheck.llm_extractor import LLMExtractor
        from crosscheck.mapper import ConceptMapper

        settings = get_settings()
        chat = ChatClient(settings)
        extractor = LLMExtractor(chat, mapper=ConceptMapper(chat) if args.mapper == "llm" else None)

    report = run_evaluation(
        extractor, args.extractor, args.data, args.limit, embedder=embedder, k=args.k
    )
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
