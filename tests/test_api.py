import pytest
from fastapi.testclient import TestClient

from crosscheck.api import create_app
from crosscheck.embeddings import HashingEmbedder
from crosscheck.generate import generate_all
from crosscheck.ingest import ingest
from crosscheck.rules_extractor import RuleExtractor

NOTE = (
    "DISCHARGE SUMMARY\n"
    "Patient: Test Patient (MRN 111111), 60-year-old male\n"
    "Diagnoses: HTN; asthma.\n"
    "Medications on discharge: Glucophage 1000 mg twice daily.\n"
    "Allergies: latex.\n"
    "No history of OA.\n"
)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("api-data")
    generate_all(out, n_docs=80, seed=3)
    ingest(out, HashingEmbedder())
    return out


@pytest.fixture()
def client(data_dir):
    return TestClient(create_app(data_dir, allow_llm=False))


def adhoc(**overrides):
    body = {
        "application": {
            "name": "Test Patient",
            "conditions": ["asthma", "osteoarthritis"],
            "medications": [{"name": "metformin", "dose": 500}],
            "allergies": [],
        },
        "notes": [{"text": NOTE}],
        "pharmacy": [{"drug": "metformin", "dose": 850, "last_dispensed": "2025-03-01"}],
    }
    body.update(overrides)
    return body


def keys(flags):
    return {(f["type"], f["field"]) for f in flags}


def test_health_and_listing(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["llm_enabled"] is False
    apps = client.get("/api/applications").json()
    assert len(apps) == health["applications"] > 10
    assert {"app_id", "name", "planted_errors"} <= set(apps[0])


def test_application_detail_and_answer_key(client):
    detail = client.get("/api/applications/A0001").json()
    assert detail["notes"] and detail["application"]["name"]
    assert isinstance(client.get("/api/applications/A0001/answer-key").json(), list)
    assert client.get("/api/applications/NOPE").status_code == 404


def test_check_stored_application_matches_the_library(client):
    detail = client.get("/api/applications/A0002").json()
    body = {
        "app_id": "A0002",
        "application": detail["application"],
        "notes": detail["notes"],
        "pharmacy": detail["pharmacy"],
    }
    result = client.post("/api/check", json=body).json()
    assert result["extractor"] == "rules"
    assert result["dropped_for_missing_evidence"] == 0
    assert all(f["id"].startswith("A0002|") for f in result["flags"])
    planted = client.get("/api/applications/A0002/answer-key").json()
    flagged_fields = {f["field"] for f in result["flags"]}
    assert all(p["field"] in flagged_fields for p in planted)


def test_adhoc_check_flags_with_evidence(client):
    result = client.post("/api/check", json=adhoc()).json()
    assert keys(result["flags"]) == {
        ("undisclosed_condition", "hypertension"),
        ("undisclosed_allergy", "latex"),
        ("contradicted_declaration", "osteoarthritis"),
        ("dose_mismatch", "metformin"),
        ("source_conflict", "metformin"),
    }
    by_type = {f["type"]: f for f in result["flags"]}
    contradicted = by_type["contradicted_declaration"]
    assert contradicted["evidence"][0]["quote"].startswith("No history of")
    assert contradicted["evidence"][0]["doc_id"] == "N1"
    assert result["facts"]["medications"] == {"metformin": [1000]}
    assert result["declared"] == {
        "conditions": ["asthma", "osteoarthritis"],
        "medications": {"metformin": 500},
        "allergies": [],
    }
    assert result["pharmacy"] == {"metformin": 850}


def test_unknown_declared_terms_are_reported_not_guessed(client):
    body = adhoc()
    body["application"]["conditions"] = ["asthma", "flarbitis"]
    body["application"]["medications"] = [{"name": "mysterypil", "dose": 5}]
    result = client.post("/api/check", json=body).json()
    assert "condition: flarbitis" in result["unmapped_declared"]
    assert "medication: mysterypil" in result["unmapped_declared"]
    assert not any(f["field"] == "flarbitis" for f in result["flags"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(extractor="magic"),
        lambda b: b.update(notes=[{"text": "x" * 20001}]),
        lambda b: b.update(notes=[{"text": "a", "doc_id": "bad id!"}]),
        lambda b: b.update(notes=[{"text": "a", "doc_id": "D1"}, {"text": "b", "doc_id": "D1"}]),
        lambda b: b.update(app_id="../etc"),
        lambda b: b["application"].update(conditions=["asthma"] * 21),
        lambda b: b["application"].update(medications=[{"name": "metformin", "dose": 0}]),
        lambda b: b.update(pharmacy=[{"drug": "metformin", "dose": 5}] * 31),
    ],
)
def test_invalid_input_is_rejected(client, mutate):
    body = adhoc()
    mutate(body)
    assert client.post("/api/check", json=body).status_code == 422


def test_llm_is_refused_unless_enabled(client):
    response = client.post("/api/check", json=adhoc(extractor="llm"))
    assert response.status_code == 403


def test_llm_path_uses_the_injected_extractor_and_maps_errors(data_dir):
    from crosscheck.config import ConfigError
    from crosscheck.costs import BudgetExceededError

    seen = []

    def good(name):
        seen.append(name)
        return RuleExtractor()

    app = TestClient(create_app(data_dir, extractor_factory=good, allow_llm=True))
    assert app.post("/api/check", json=adhoc(extractor="llm")).status_code == 200
    assert seen == ["llm"]

    for error, status in [
        (ConfigError("no key"), 503),
        (BudgetExceededError("cap"), 429),
        (RuntimeError("boom"), 502),
    ]:

        def failing(name, error=error):
            raise error

        bad = TestClient(create_app(data_dir, extractor_factory=failing, allow_llm=True))
        assert bad.post("/api/check", json=adhoc(extractor="llm")).status_code == status


def test_reviews_round_trip(data_dir):
    client = TestClient(create_app(data_dir, allow_llm=False))
    flag = "A0001|undisclosed_allergy|latex"
    assert client.put("/api/reviews", json={"flag_id": flag, "decision": "Confirm"}).json() == {
        flag: "Confirm"
    }
    assert client.get("/api/reviews").json()[flag] == "Confirm"
    assert client.put("/api/reviews", json={"flag_id": flag, "decision": "—"}).json() == {}
    assert (
        client.put("/api/reviews", json={"flag_id": flag, "decision": "Maybe"}).status_code == 422
    )


def test_index_is_served(client):
    response = client.get("/")
    assert response.status_code == 200 and "<html" in response.text


def test_application_detail_reads_pharmacy_rows_from_the_database(data_dir):
    from crosscheck.pharmacy_db import PharmacyDB, db_path

    client = TestClient(create_app(data_dir, allow_llm=False))
    db = PharmacyDB(db_path(data_dir))
    for app_id in ("A0001", "A0002", "A0003"):
        detail = client.get(f"/api/applications/{app_id}").json()
        expected = [
            {"drug": r.drug, "dose": r.dose, "unit": r.unit, "last_dispensed": r.last_dispensed}
            for r in db.rows_for_mrn(detail["mrn"])
        ]
        assert detail["pharmacy"] == expected


def test_pharmacy_lookup_endpoint(client):
    mrn = client.get("/api/applications/A0001").json()["mrn"]
    found = client.get(f"/api/pharmacy/{mrn}").json()
    assert found["mrn"] == mrn
    assert found["rows"] == client.get("/api/applications/A0001").json()["pharmacy"]
    assert client.get("/api/pharmacy/000000").json() == {"mrn": "000000", "rows": []}


@pytest.mark.parametrize("bad", ["a b", "x" * 21, "1;DROP", "%27OR%271"])
def test_pharmacy_lookup_rejects_odd_identifiers(client, bad):
    assert client.get(f"/api/pharmacy/{bad}").status_code in (404, 422)


def test_health_reports_database_counts(client):
    health = client.get("/api/health").json()
    assert health["pharmacy"]["rows"] > 0 and health["pharmacy"]["patients"] > 0


def test_detail_includes_the_raw_form_and_notes_read_from_the_store(client):
    detail = client.get("/api/applications/A0001").json()
    assert detail["form_text"].startswith("LIFE INSURANCE APPLICATION")
    assert "Reference: A0001" in detail["form_text"]
    assert detail["unmapped_declared"] == []
    assert all("MRN " + detail["mrn"] in n["text"] for n in detail["notes"])


def test_declared_terms_are_shown_as_the_applicant_wrote_them(client):
    forms = [client.get(f"/api/applications/A{i:04d}").json() for i in range(1, 40)]
    written = {c for f in forms for c in f["application"]["conditions"]}
    assert "HTN" in written or "COPD" in written  # abbreviations survive, not canonicalised


def test_health_reports_the_store(client):
    store = client.get("/api/health").json()["store"]
    assert store["applications"] > 10 and store["chunks"] > store["notes"]
    assert store["embedder"].startswith("hashing-v2-") and store["errors"] == 0


def test_ingest_issues_endpoint(client):
    body = client.get("/api/ingest/issues").json()
    assert body["issues"] == [] and body["meta"]["embedder"].startswith("hashing")


def test_retrieve_shows_what_a_check_would_read(client):
    result = client.get("/api/applications/A0002/retrieve").json()
    assert result["hits"] and 0 < result["chars_sent"] < result["chars_total"]
    assert {"doc_id", "line", "text", "score", "queries"} <= set(result["hits"][0])
    assert len(result["queries"]) > 4


def test_retrieve_searches_with_a_free_text_query(client):
    detail = client.get("/api/applications/A0002/retrieve", params={"q": "allergies", "k": 2})
    hits = detail.json()["hits"]
    assert 1 <= len(hits) <= 2 and detail.json()["queries"] == ["allergies"]
    assert any("llerg" in h["text"] or "NKDA" in h["text"] for h in hits)


@pytest.mark.parametrize("params", [{"k": 0}, {"k": 11}, {"q": "x" * 201}, {"k": "many"}])
def test_retrieve_rejects_bad_parameters(client, params):
    assert client.get("/api/applications/A0002/retrieve", params=params).status_code == 422


def test_retrieve_unknown_application_is_404(client):
    assert client.get("/api/applications/NOPE/retrieve").status_code == 404


def test_rag_check_matches_reading_everything_and_reports_what_was_sent(client):
    detail = client.get("/api/applications/A0002").json()
    body = {
        "app_id": "A0002",
        "application": detail["application"],
        "notes": detail["notes"],
        "pharmacy": detail["pharmacy"],
    }
    full = client.post("/api/check", json=body).json()
    rag = client.post("/api/check", json={**body, "retrieval": "rag", "top_k": 3}).json()
    assert full["retrieval"] is None
    assert {(f["type"], f["field"]) for f in rag["flags"]} == keys(full["flags"])
    info = rag["retrieval"]
    assert info["k"] == 3 and 0 < info["chars_sent"] < info["chars_total"]
    assert all(h["text"] for h in info["hits"])


def test_rag_check_rejects_a_bad_top_k(client):
    assert client.post("/api/check", json=adhoc(retrieval="rag", top_k=0)).status_code == 422
    assert client.post("/api/check", json=adhoc(retrieval="magic")).status_code == 422


def test_an_embedder_that_is_not_allowed_is_a_403_not_a_crash(data_dir):
    def refuse(name):
        raise PermissionError("needs ALLOW_LLM=1")

    app = TestClient(create_app(data_dir, allow_llm=False, embedder_factory=refuse))
    assert app.get("/api/applications/A0002/retrieve").status_code == 403
    assert app.post("/api/check", json=adhoc(retrieval="rag")).status_code == 403


def test_unverified_flag_tells_the_reviewer_which_note_terms_could_not_be_read(client):
    body = adhoc(notes=[{"text": "Patient: T (MRN 1), 40-year-old male\nDiagnoses: flarbitis.\n"}])
    body["application"]["conditions"] = ["asthma"]
    result = client.post("/api/check", json=body).json()
    flag = next(f for f in result["flags"] if f["type"] == "unverified_declaration")
    assert flag["unmapped"] == ["flarbitis"]
    assert "flarbitis" in flag["message"] and "could not map" in flag["message"]


def test_every_check_is_recorded_with_its_flags_and_versions(data_dir):
    usage = iter([(100, 10, 0.50), (140, 30, 0.75)])
    app = TestClient(
        create_app(
            data_dir,
            extractor_factory=lambda name: RuleExtractor(),
            allow_llm=True,
            usage_probe=lambda: next(usage),
            model_label=lambda: "test-model",
        )
    )
    result = app.post("/api/check", json=adhoc(extractor="llm")).json()
    run = app.get(f"/api/audit/runs/{result['run_id']}").json()
    assert run["extractor"] == "llm" and run["model"] == "test-model"
    assert len(run["prompt_version"]) == 8 and run["prompt_version"] != "rules"
    assert (run["input_tokens"], run["output_tokens"]) == (40, 20)
    assert run["cost_usd"] == pytest.approx(0.25)
    assert run["n_flags"] == len(result["flags"]) == len(run["flags"])
    assert {f["id"] for f in run["flags"]} == {f["id"] for f in result["flags"]}
    assert run["flags"][0]["evidence"] and run["duration_ms"] >= 0


def test_a_rules_check_is_recorded_without_cost_or_model(client):
    result = client.post("/api/check", json=adhoc(app_id="A0002")).json()
    run = client.get(f"/api/audit/runs/{result['run_id']}").json()
    assert (run["model"], run["prompt_version"], run["cost_usd"]) == (None, "rules", 0.0)
    listed = client.get("/api/audit/runs", params={"app_id": "A0002", "limit": 5}).json()
    assert listed[0]["run_id"] == result["run_id"]


def test_a_failed_check_is_not_recorded(data_dir):
    def failing(name):
        raise RuntimeError("boom")

    app = TestClient(create_app(data_dir, extractor_factory=failing, allow_llm=True))
    before = len(app.get("/api/audit/runs", params={"limit": 200}).json())
    assert app.post("/api/check", json=adhoc(extractor="llm")).status_code == 502
    assert len(app.get("/api/audit/runs", params={"limit": 200}).json()) == before


def test_audit_endpoints_validate_their_inputs(client):
    assert client.get("/api/audit/runs/nope").status_code == 422
    assert client.get("/api/audit/runs/" + "0" * 12).status_code == 404
    assert client.get("/api/audit/runs", params={"limit": 0}).status_code == 422
    assert client.get("/api/audit/runs", params={"app_id": "a b"}).status_code == 422


def test_decisions_are_logged_with_history_and_feed_review_stats(data_dir):
    app = TestClient(create_app(data_dir, allow_llm=False))
    result = app.post("/api/check", json=adhoc(app_id="A0003")).json()
    flag = result["flags"][0]["id"]
    app.put(
        "/api/reviews", json={"flag_id": flag, "decision": "Confirm", "run_id": result["run_id"]}
    )
    app.put("/api/reviews", json={"flag_id": flag, "decision": "Dismiss"})
    history = app.get("/api/reviews/history", params={"flag_id": flag}).json()
    assert [h["decision"] for h in history] == ["Confirm", "Dismiss"]
    assert history[0]["run_id"] == result["run_id"]
    kind = flag.split("|")[1]
    stats = app.get("/api/review-stats").json()["by_type"][kind]
    assert stats["Dismiss"] >= 1 and 0 < stats["dismiss_rate"] <= 1
    bad = app.put("/api/reviews", json={"flag_id": flag, "decision": "Confirm", "run_id": "x"})
    assert bad.status_code == 422


def test_old_reviews_json_is_imported_when_the_audit_log_is_new(tmp_path):
    generate_all(tmp_path, n_docs=40, seed=8)
    ingest(tmp_path, HashingEmbedder())
    (tmp_path / "reviews.json").write_text('{"A0001|dose_mismatch|metformin": "Confirm"}')
    app = TestClient(create_app(tmp_path, allow_llm=False))
    assert app.get("/api/reviews").json() == {"A0001|dose_mismatch|metformin": "Confirm"}
