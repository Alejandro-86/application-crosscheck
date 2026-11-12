import json

import pytest

from crosscheck import gate
from crosscheck.facts import ExtractionResult
from crosscheck.gate import (
    LLM_BASELINE,
    RULES_BASELINE,
    Check,
    compare_to_baseline,
    measure_rules,
    render,
    run_rules_gate,
)
from crosscheck.rules_extractor import RuleExtractor


class DropMedications:
    """The rules extractor with every medication forgotten: a regression to catch."""

    def __init__(self):
        self._inner = RuleExtractor()

    def extract(self, doc_id, text):
        result = self._inner.extract(doc_id, text)
        facts = [f for f in result.facts if f.kind != "medication"]
        return ExtractionResult(doc_id, facts, result.unmapped)


def test_the_committed_baseline_passes_on_the_current_code():
    checks = run_rules_gate()
    assert checks and all(c.ok for c in checks), render([c for c in checks if not c.ok])


def test_a_real_regression_fails_the_gate(tmp_path):
    current = measure_rules(DropMedications(), tmp_path)
    baseline = json.loads(RULES_BASELINE.read_text())["metrics"]
    failed = {c.name for c in compare_to_baseline(current, baseline, 0.005) if not c.ok}
    assert "extraction.medication.recall" in failed and "flags.recall" in failed


def test_a_metric_may_dip_within_the_tolerance_but_no_further():
    baseline = {"flags.recall": 0.90}
    assert compare_to_baseline({"flags.recall": 0.896}, baseline, 0.005)[0].ok
    assert not compare_to_baseline({"flags.recall": 0.89}, baseline, 0.005)[0].ok
    assert compare_to_baseline({"flags.recall": 0.99}, baseline, 0.005)[0].ok  # improving is fine


def test_text_sent_is_lower_is_better():
    baseline = {"retrieval.top3.text_share": 0.70}
    assert compare_to_baseline({"retrieval.top3.text_share": 0.65}, baseline, 0.01)[0].ok
    assert not compare_to_baseline({"retrieval.top3.text_share": 0.80}, baseline, 0.01)[0].ok


def test_missing_and_unrecorded_metrics_fail_and_unmeasurable_ones_do_not():
    checks = compare_to_baseline({"new.metric": 1.0}, {"old.metric": 0.9, "empty": None}, 0.005)
    by_name = {c.name: c for c in checks}
    assert not by_name["old.metric"].ok  # was recorded, no longer measured
    assert by_name["empty"].ok  # nothing was measurable when recorded
    assert not by_name["new.metric"].ok  # new metrics need a deliberate --update


def test_the_report_marks_failures():
    text = render(
        [Check("flags.recall", 0.9, 0.5, False), Check("scraper.exact_rate", 1.0, 1.0, True)]
    )
    assert "FAIL" in text and "ok" in text and "0.5000" in text


def test_update_writes_a_baseline_that_then_passes(tmp_path):
    path = tmp_path / "baseline.json"
    run_rules_gate(path, update=True)
    written = json.loads(path.read_text())
    assert written["_meta"]["corpus"] == gate.CORPUS and "flags.recall" in written["metrics"]
    assert all(c.ok for c in run_rules_gate(path))


def test_a_failing_gate_exits_non_zero(monkeypatch):
    monkeypatch.setattr(
        gate, "run_rules_gate", lambda **_: [Check("flags.recall", 0.9, 0.1, False)]
    )
    monkeypatch.setattr("sys.argv", ["gate"])
    with pytest.raises(SystemExit) as stopped:
        gate.main()
    assert stopped.value.code == 1


def test_the_model_baseline_records_what_it_was_measured_with():
    recorded = json.loads(LLM_BASELINE.read_text())
    assert {"model", "prompt_version", "limit", "data"} <= set(recorded["_meta"])
    assert len(recorded["_meta"]["prompt_version"]) == 8
    assert recorded["metrics"]["flags.recall"] is not None
    assert set(recorded["metrics"]) == {
        f"extraction.{kind}.{kind_metric}"
        for kind in ("condition", "negated_condition", "medication", "allergy")
        for kind_metric in ("precision", "recall")
    } | {"flags.precision", "flags.recall"}
