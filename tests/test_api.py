import pytest
from fastapi.testclient import TestClient

from crosscheck.api import create_app
from crosscheck.generate import generate_all
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
