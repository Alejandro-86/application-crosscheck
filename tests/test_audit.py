import json
import sqlite3

import pytest

from crosscheck.audit import AuditDB, RunRecord, prompt_version
from crosscheck.costs import UsageLog


def flag(flag_id="A1|undisclosed_allergy|latex", severity="discrepancy"):
    kind, field_name = flag_id.split("|")[1:]
    return {
        "id": flag_id,
        "type": kind,
        "field": field_name,
        "severity": severity,
        "message": "m",
        "evidence": [{"source": "notes", "doc_id": "D1", "quote": "Allergies: latex."}],
    }


@pytest.fixture()
def db(tmp_path):
    return AuditDB(tmp_path / "audit.db")


def test_a_run_round_trips_with_its_flags_and_evidence(db):
    run_id = db.record_run(
        RunRecord(
            app_id="A1",
            extractor="llm",
            retrieval="rag",
            n_flags=1,
            flags=[flag()],
            model="some-model",
            prompt_version="abcd1234",
            top_k=3,
            embedder="hashing-v2-2048",
            chars_sent=40,
            chars_total=100,
            reasons=["flags"],
            unmapped=["flarbitis"],
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
            duration_ms=12,
        )
    )
    run = db.run(run_id)
    assert run["model"] == "some-model" and run["prompt_version"] == "abcd1234"
    assert run["reasons"] == ["flags"] and run["unmapped"] == ["flarbitis"]
    assert (run["chars_sent"], run["chars_total"], run["cost_usd"]) == (40, 100, 0.001)
    assert run["flags"][0]["evidence"][0]["quote"] == "Allergies: latex."
    assert db.run("0" * 12) is None


def test_runs_are_listed_newest_first_and_filtered_by_application(db):
    first = db.record_run(RunRecord("A1", "rules", "all", 0))
    db.record_run(RunRecord("A2", "rules", "all", 0))
    third = db.record_run(RunRecord("A1", "rules", "all", 0))
    assert [r["run_id"] for r in db.runs("A1")] == [third, first]
    assert len(db.runs()) == 3 and len(db.runs(limit=2)) == 2


def test_status_is_the_only_thing_updated_in_place(db):
    run_id = db.record_run(RunRecord("A1", "rules", "all", 0, status="awaiting_review"))
    db.set_status(run_id, "reviewed")
    assert db.run(run_id)["status"] == "reviewed"


def test_decisions_are_an_append_only_history_and_the_latest_wins(db):
    fid = "A1|undisclosed_allergy|latex"
    db.add_decision(fid, "Confirm", "r1")
    db.add_decision(fid, "Dismiss", "r2")
    assert db.decisions() == {fid: "Dismiss"}
    assert [(h["decision"], h["run_id"]) for h in db.decision_history(fid)] == [
        ("Confirm", "r1"),
        ("Dismiss", "r2"),
    ]
    db.add_decision(fid, "—")
    assert db.decisions() == {}
    assert len(db.decision_history(fid)) == 3  # clearing is an event, not a deletion


def test_invalid_decisions_are_refused(db):
    with pytest.raises(ValueError):
        db.add_decision("A1|x|y", "Maybe")
    with pytest.raises(sqlite3.IntegrityError), db._connect() as conn:
        conn.execute(
            "INSERT INTO decision_events (ts, flag_id, decision) VALUES ('t', 'f', 'Maybe')"
        )


def test_stats_count_current_decisions_per_flag_type(db):
    db.add_decision("A1|undisclosed_allergy|latex", "Confirm")
    db.add_decision("A2|undisclosed_allergy|nuts", "Dismiss")
    db.add_decision("A3|undisclosed_allergy|aspirin", "Confirm")
    db.add_decision("A3|undisclosed_allergy|aspirin", "Dismiss")  # changed its mind
    db.add_decision("A4|dose_mismatch|metformin", "Confirm")
    db.add_decision("A4|dose_mismatch|metformin", "—")  # cleared: not counted
    assert db.decision_stats() == {"undisclosed_allergy": {"Confirm": 1, "Dismiss": 2}}


def test_ad_hoc_flag_ids_without_three_parts_are_still_stored(db):
    db.add_decision("custom-id", "Confirm")
    assert db.decisions() == {"custom-id": "Confirm"}


def test_queries_are_parameterised(db):
    db.add_decision("A1|x|y", "Confirm")
    assert db.runs("1' OR '1'='1") == []
    assert db.decision_history("x' OR '1'='1") == []
    assert db.decisions() == {"A1|x|y": "Confirm"}


def test_legacy_reviews_json_is_imported_once(tmp_path):
    legacy = tmp_path / "reviews.json"
    legacy.write_text(json.dumps({"A1|dose_mismatch|metformin": "Confirm", "A2|a|b": "—"}))
    db = AuditDB(tmp_path / "audit.db")
    assert db.import_legacy_reviews(legacy) == 1
    assert db.decisions() == {"A1|dose_mismatch|metformin": "Confirm"}
    assert db.import_legacy_reviews(legacy) == 0
    assert AuditDB(tmp_path / "other.db").import_legacy_reviews(tmp_path / "missing.json") == 0


def test_prompt_version_changes_when_the_prompt_changes():
    assert prompt_version("a") == prompt_version("a") != prompt_version("b")
    assert len(prompt_version("a")) == 8


def test_usage_totals_sum_tokens_and_cost(tmp_path):
    log = UsageLog(tmp_path / "usage.jsonl", 10)
    assert log.totals() == (0, 0, 0.0)
    log.record(kind="chat", model="m", input_tokens=10, output_tokens=4, cost=0.5, cached=False)
    log.record(kind="chat", model="m", input_tokens=0, output_tokens=0, cost=None, cached=True)
    assert log.totals() == (10, 4, 0.5)
