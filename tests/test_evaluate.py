import pytest

from crosscheck.embeddings import HashingEmbedder
from crosscheck.evaluate import (
    PRF,
    RandomEmbedder,
    format_report,
    format_sweep,
    run_evaluation,
)
from crosscheck.facts import ExtractionResult
from crosscheck.generate import main
from crosscheck.ingest import ingest
from crosscheck.rules_extractor import RuleExtractor


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("gen")
    import sys

    argv = sys.argv
    sys.argv = ["generate", "--n-docs", "120", "--seed", "11", "--out", str(out)]
    try:
        main()
    finally:
        sys.argv = argv
    ingest(out, HashingEmbedder())
    return out


class DropMedications:
    """Wraps the rule extractor but forgets every medication, to prove the scores notice."""

    def __init__(self):
        self._inner = RuleExtractor()

    def extract(self, doc_id, text):
        result = self._inner.extract(doc_id, text)
        return ExtractionResult(
            doc_id, [f for f in result.facts if f.kind != "medication"], result.unmapped
        )


def test_prf_arithmetic():
    prf = PRF()
    prf.add({1, 2, 3}, {2, 3, 4})
    assert (prf.tp, prf.fp, prf.fn) == (2, 1, 1)
    assert prf.precision == pytest.approx(2 / 3)
    assert prf.recall == pytest.approx(2 / 3)
    assert PRF().precision is None


def test_perfect_extractor_scores_one_everywhere(data_dir):
    report = run_evaluation(RuleExtractor(), "rules", data_dir)
    assert report.overall.f1 == 1.0
    assert all(p.fp == 0 and p.fn == 0 for p in report.extraction.values())
    assert report.false_positives == [] and report.false_negatives == []
    assert report.planted_errors > 20


def test_missing_extraction_lowers_flag_recall(data_dir):
    report = run_evaluation(DropMedications(), "no-meds", data_dir)
    assert report.extraction["medication"].recall == 0.0
    assert report.overall.recall < 1.0
    assert report.false_negatives


def test_limit_restricts_applications(data_dir):
    report = run_evaluation(RuleExtractor(), "rules", data_dir, limit=5)
    assert report.n_applications == 5


def test_report_is_readable_markdown(data_dir):
    text = format_report(run_evaluation(RuleExtractor(), "rules", data_dir, limit=10))
    assert text.startswith("# Results: rules extractor")
    assert "| overall |" in text.replace("**", "")
    assert "undisclosed_condition" in text


def test_the_report_states_how_many_forms_the_scraper_got_exactly_right(data_dir):
    report = run_evaluation(RuleExtractor(), "rules", data_dir, limit=3)
    assert report.scraper_total > 50 and report.scraper_exact == report.scraper_total
    assert report.chars_sent == report.chars_total > 0
    assert "parsed from their raw forms exactly as the truth file says" in format_report(report)


def test_retrieval_sends_less_text_and_a_wider_k_recovers_more_flags(data_dir):
    embedder = HashingEmbedder()
    narrow = run_evaluation(RuleExtractor(), "rules", data_dir, embedder=embedder, k=1)
    wide = run_evaluation(RuleExtractor(), "rules", data_dir, embedder=embedder, k=5)
    full = run_evaluation(RuleExtractor(), "rules", data_dir)
    assert narrow.chars_sent < wide.chars_sent <= full.chars_sent == full.chars_total
    assert narrow.overall.fn >= wide.overall.fn
    assert narrow.overall.fp == 0 and wide.overall.fp == 0
    assert wide.overall.recall > 0.9


def test_sweep_table_has_one_row_per_setting(data_dir):
    embedder = HashingEmbedder()
    reports = [run_evaluation(RuleExtractor(), "rules", data_dir, limit=10)]
    reports.append(run_evaluation(RuleExtractor(), "rules", data_dir, 10, embedder=embedder, k=2))
    text = format_sweep(reports)
    assert "all text" in text and "top-2 per query" in text
    assert text.count("\n| ") == 2 + 1  # header, separator is not counted, two rows


def test_an_embedder_that_does_not_match_the_store_is_refused(data_dir):
    with pytest.raises(ValueError, match="Run python -m crosscheck.ingest"):
        run_evaluation(RuleExtractor(), "rules", data_dir, embedder=HashingEmbedder(dim=64))


def test_retrieval_beats_the_random_control_at_the_same_k(data_dir):
    real = run_evaluation(RuleExtractor(), "rules", data_dir, embedder=HashingEmbedder(), k=1)
    chance = run_evaluation(
        RuleExtractor(),
        "rules",
        data_dir,
        embedder=RandomEmbedder(),
        k=1,
        use_store_index=False,
    )
    assert real.overall.f1 > chance.overall.f1 + 0.05
