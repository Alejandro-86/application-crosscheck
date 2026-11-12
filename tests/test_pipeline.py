from collections import defaultdict

from crosscheck.applications import generate_applications
from crosscheck.compare import compare
from crosscheck.corpus import LoadedDocument
from crosscheck.oracle import doc_truth, oracle_facts
from crosscheck.pharmacy import generate_pharmacy
from crosscheck.pipeline import check_application, index_documents
from crosscheck.rules_extractor import RuleExtractor
from crosscheck.synthetic import generate_corpus


def build():
    patients, docs = generate_corpus(200, 42)
    pharmacy = generate_pharmacy(patients, 42)
    apps = generate_applications(patients, docs, pharmacy, 42)
    loaded = [
        LoadedDocument(d.doc_id, d.text, d.patient_id, d.doc_type, d.fields_present, d.hard_cases)
        for d in docs
    ]
    return patients, loaded, pharmacy, apps


def test_rule_extractor_matches_ground_truth_on_every_document():
    patients, loaded, _, _ = build()
    by_id = {p.patient_id: p for p in patients}
    extractor = RuleExtractor()
    for doc in loaded:
        result = extractor.extract(doc.doc_id, doc.text)
        got = {(f.kind, f.value, f.dose) for f in result.facts}
        assert got == doc_truth(by_id[doc.patient_id], doc.fields_present), doc.doc_id
        assert not result.unmapped


def test_pipeline_with_rule_extractor_reproduces_the_oracle_flags():
    patients, loaded, pharmacy, apps = build()
    by_id = {p.patient_id: p for p in patients}
    by_mrn = index_documents(loaded)
    rows = defaultdict(list)
    for row in pharmacy:
        rows[row.mrn].append(row)
    extractor = RuleExtractor()
    total = 0
    for app in apps:
        docs = by_mrn[app.mrn]
        expected = {
            f.key for f in compare(app, oracle_facts(by_id[app.patient_id], docs), rows[app.mrn])
        }
        result = check_application(app, docs, rows[app.mrn], extractor)
        assert {f.key for f in result.flags} == expected, app.app_id
        assert result.dropped == []
        total += len(expected)
    assert total > 50


def test_every_planted_error_is_flagged():
    patients, loaded, pharmacy, apps = build()
    by_mrn = index_documents(loaded)
    rows = defaultdict(list)
    for row in pharmacy:
        rows[row.mrn].append(row)
    expected_types = {
        "undisclosed_condition": {"undisclosed_condition"},
        "false_declaration": {"contradicted_declaration", "unverified_declaration"},
        "wrong_dose": {"dose_mismatch"},
        "undisclosed_medication": {"undisclosed_medication"},
        "undisclosed_allergy": {"undisclosed_allergy"},
    }
    extractor = RuleExtractor()
    for app in apps:
        flags = check_application(app, by_mrn[app.mrn], rows[app.mrn], extractor).flags
        for error in app.planted_errors:
            assert any(
                f.field == error.field and f.type in expected_types[error.type] for f in flags
            ), (app.app_id, error)
