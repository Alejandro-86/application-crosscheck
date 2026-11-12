import pytest

from crosscheck.evaluate import PRF, format_report, run_evaluation
from crosscheck.facts import ExtractionResult
from crosscheck.generate import main
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
