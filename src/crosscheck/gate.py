"""Regression gate: fail if quality drops below a committed baseline.

    python -m crosscheck.gate                 # free, runs in CI: rules extractor on a fixed corpus
    python -m crosscheck.gate --update        # rewrite the baseline (do this on purpose, in a PR)
    python -m crosscheck.gate --llm           # manual, costs credits: the model on data/generated
    python -m crosscheck.gate --llm --update  # record a new model baseline

The free gate covers everything that is not the model: the scraper, extraction rules, the
terminology table, retrieval, the comparison rules and the grounding check. The model gate covers
a prompt, model or mapping change and is run by hand, because CI has no API key. A metric may not
fall more than the tolerance below its baseline (or, for text sent, rise above it).
"""

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from crosscheck.config import REPO_ROOT
from crosscheck.embeddings import HashingEmbedder
from crosscheck.evaluate import EvalReport, run_evaluation
from crosscheck.generate import generate_all
from crosscheck.ingest import ingest
from crosscheck.pipeline import Extractor

RULES_BASELINE = REPO_ROOT / "docs" / "gate-baseline.json"
LLM_BASELINE = REPO_ROOT / "docs" / "gate-baseline-llm.json"
CORPUS = {"n_docs": 200, "seed": 7, "hard_fraction": 0.4}
RULES_TOLERANCE = 0.005
LLM_TOLERANCE = 0.03
LOWER_IS_BETTER = {"retrieval.top3.text_share"}


@dataclass
class Check:
    name: str
    baseline: float | None
    current: float | None
    ok: bool


def metrics_from(report: EvalReport) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for kind, prf in report.extraction.items():
        out[f"extraction.{kind}.precision"] = prf.precision
        out[f"extraction.{kind}.recall"] = prf.recall
    out["flags.precision"] = report.overall.precision
    out["flags.recall"] = report.overall.recall
    return {k: None if v is None else round(v, 4) for k, v in out.items()}


def measure_rules(extractor: Extractor, work_dir: Path) -> dict[str, float | None]:
    """Metrics for an extractor on the fixed corpus, reading all text and with retrieval."""
    generate_all(work_dir, **CORPUS)
    embedder = HashingEmbedder()
    ingest(work_dir, embedder)
    full = run_evaluation(extractor, "gate", work_dir)
    rag = run_evaluation(extractor, "gate", work_dir, embedder=embedder, k=3)
    metrics = metrics_from(full)
    metrics["scraper.exact_rate"] = round(full.scraper_exact / full.scraper_total, 4)
    metrics["retrieval.top3.flag_recall"] = (
        None if rag.overall.recall is None else round(rag.overall.recall, 4)
    )
    metrics["retrieval.top3.text_share"] = round(rag.chars_sent / rag.chars_total, 4)
    return metrics


def compare_to_baseline(
    current: dict[str, float | None], baseline: dict[str, float | None], tolerance: float
) -> list[Check]:
    checks = []
    for name, base in sorted(baseline.items()):
        now = current.get(name)
        if base is None:
            ok = True  # there was nothing to measure when the baseline was recorded
        elif now is None:
            ok = False
        elif name in LOWER_IS_BETTER:
            ok = now <= base + tolerance
        else:
            ok = now >= base - tolerance
        checks.append(Check(name, base, now, ok))
    for name in sorted(set(current) - set(baseline)):
        checks.append(Check(name, None, current[name], False))  # unrecorded: update the baseline
    return checks


def render(checks: list[Check]) -> str:
    def fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    lines = [f"{'metric':<38}{'baseline':>10}{'current':>10}  result"]
    for c in checks:
        lines.append(
            f"{c.name:<38}{fmt(c.baseline):>10}{fmt(c.current):>10}  {'ok' if c.ok else 'FAIL'}"
        )
    return "\n".join(lines)


def load_baseline(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"No baseline at {path}. Create one with --update.")
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline(path: Path, metrics: dict, meta: dict) -> None:
    payload = {"_meta": meta, "metrics": metrics}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_rules_gate(baseline_path: Path = RULES_BASELINE, update: bool = False) -> list[Check]:
    from crosscheck.rules_extractor import RuleExtractor

    with tempfile.TemporaryDirectory() as work:
        current = measure_rules(RuleExtractor(), Path(work))
    if update:
        write_baseline(baseline_path, current, {"corpus": CORPUS, "extractor": "rules"})
        return compare_to_baseline(current, current, RULES_TOLERANCE)
    return compare_to_baseline(current, load_baseline(baseline_path)["metrics"], RULES_TOLERANCE)


def run_llm_gate(data: Path, limit: int, update: bool, baseline_path: Path = LLM_BASELINE):
    from crosscheck.config import get_settings
    from crosscheck.llm import ChatClient
    from crosscheck.llm_extractor import PROMPT_VERSION, LLMExtractor

    settings = get_settings()
    report = run_evaluation(LLMExtractor(ChatClient(settings)), "llm", data, limit)
    current = metrics_from(report)
    meta = {
        "model": settings.chat_model,
        "prompt_version": PROMPT_VERSION,
        "limit": limit,
        "data": str(data),
        "note": "recorded from a live run; replies are cached, so a rerun is free",
    }
    if update:
        write_baseline(baseline_path, current, meta)
        return compare_to_baseline(current, current, LLM_TOLERANCE), meta
    recorded = load_baseline(baseline_path)
    return compare_to_baseline(current, recorded["metrics"], LLM_TOLERANCE), recorded["_meta"] | {
        "now_prompt_version": PROMPT_VERSION,
        "now_model": settings.chat_model,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail if quality drops below the baseline.")
    parser.add_argument("--llm", action="store_true", help="run the model gate (costs credits)")
    parser.add_argument("--update", action="store_true", help="record the current numbers")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    if args.llm:
        checks, meta = run_llm_gate(args.data, args.limit, args.update)
        if meta.get("now_prompt_version") not in (None, meta.get("prompt_version")):
            print(
                f"Note: prompt changed ({meta['prompt_version']} -> {meta['now_prompt_version']})."
            )
        if meta.get("now_model") not in (None, meta.get("model")):
            print(f"Note: model changed ({meta['model']} -> {meta['now_model']}).")
    else:
        checks = run_rules_gate(update=args.update)
    print(render(checks))
    failed = [c for c in checks if not c.ok]
    if args.update:
        print("Baseline written.")
    elif failed:
        print(f"\nGate FAILED on {len(failed)} metric(s).", file=sys.stderr)
        raise SystemExit(1)
    else:
        print("\nGate passed.")


if __name__ == "__main__":
    main()
