import hashlib

import pytest

from crosscheck.corpus import LoadedDocument
from crosscheck.embeddings import HashingEmbedder
from crosscheck.evaluate import format_report, run_evaluation
from crosscheck.generate import generate_all
from crosscheck.ingest import ingest
from crosscheck.rules_extractor import RuleExtractor
from crosscheck.synthetic import (
    CONDITION_FORMS,
    CONDITION_SYNONYMS,
    UNLISTED_PARAPHRASES,
    generate_corpus,
)
from crosscheck.terminology import normalise_condition


def digest(documents):
    return hashlib.sha256("".join(d.text for d in documents).encode()).hexdigest()


def hard_docs(seed=5, n=120, fraction=0.6):
    patients, documents = generate_corpus(n, seed, fraction)
    by_id = {p.patient_id: p for p in patients}
    return [(by_id[d.patient_id], d) for d in documents if d.doc_type == "narrative_letter"]


def test_easy_notes_are_unchanged_by_the_hard_option():
    _, easy = generate_corpus(60, 5)
    # Recorded before the hard option existed: the default output must never drift.
    assert digest(easy).startswith("1f14f084aed941c2")
    _, mixed = generate_corpus(60, 5, 0.5)
    assert [d.doc_id for d in easy] == [d.doc_id for d in mixed]
    for before, after in zip(easy, mixed, strict=True):
        if after.doc_type != "narrative_letter":
            assert before.text == after.text


def test_hard_generation_is_deterministic_and_the_fraction_is_respected():
    _, a = generate_corpus(100, 3, 0.4)
    _, b = generate_corpus(100, 3, 0.4)
    assert digest(a) == digest(b)
    share = sum(d.doc_type == "narrative_letter" for d in a) / len(a)
    assert 0.25 < share < 0.55
    _, none = generate_corpus(100, 3, 0.0)
    assert all(d.doc_type != "narrative_letter" for d in none)


def test_a_hard_note_still_states_every_true_condition_and_medication():
    pairs = hard_docs()
    assert len(pairs) > 20
    for patient, doc in pairs:
        text = doc.text.lower()
        assert f"mrn {patient.mrn}" in text
        for condition in patient.conditions:
            wordings = {
                *CONDITION_FORMS[condition],
                *CONDITION_SYNONYMS.get(condition, []),
                UNLISTED_PARAPHRASES.get(condition, ""),
            }
            assert any(w and w.lower() in text for w in wordings), (doc.doc_id, condition)
        for med in patient.medications:
            assert med.name in text or med.brand.lower() in text


def test_trap_sentences_never_describe_the_patients_own_facts():
    seen = {"family_history": 0, "discontinued_medication": 0}
    for patient, doc in hard_docs():
        if "family_history" in doc.hard_cases:
            seen["family_history"] += 1
            line = next(s for s in doc.text.split(". ") if "Family history" in s)
            relative_condition = [c for c in CONDITION_FORMS if CONDITION_FORMS[c][0] in line]
            assert relative_condition and not set(relative_condition) & set(patient.conditions)
        if "discontinued_medication" in doc.hard_cases:
            seen["discontinued_medication"] += 1
            line = next(s for s in doc.text.split("\n")[3].split(". ") if "stopped" in s)
            assert not any(m.name in line for m in patient.medications)
    assert all(count > 0 for count in seen.values())


def test_doses_in_grams_are_exact_conversions():
    found = 0
    for patient, doc in hard_docs():
        if "dose_in_grams" in doc.hard_cases:
            found += 1
            assert any(m.dose in (500, 1000) for m in patient.medications)
            assert "0.5 g" in doc.text or "1 g" in doc.text
    assert found > 0


def test_listed_synonyms_map_and_unlisted_paraphrases_do_not():
    for canonical, synonyms in CONDITION_SYNONYMS.items():
        assert all(normalise_condition(s) == canonical for s in synonyms)
    assert all(normalise_condition(p) is None for p in UNLISTED_PARAPHRASES.values())


def test_offline_embedder_follows_a_listed_synonym_but_not_an_unlisted_one():
    from crosscheck.embeddings import cosine

    e = HashingEmbedder()
    query, listed, unlisted = e.embed(
        ["hypertension", "history of high blood pressure", "history of raised pressure readings"]
    )
    assert cosine(query, listed) > 0.3 and cosine(query, unlisted) == pytest.approx(0, abs=1e-9)


def test_the_rules_baseline_cannot_read_narrative_letters():
    pairs = hard_docs()
    missed = 0
    for patient, doc in pairs:
        loaded = LoadedDocument(doc.doc_id, doc.text, patient.patient_id, "x", [], [])
        facts = RuleExtractor().extract(loaded.doc_id, loaded.text).facts
        missed += not any(f.kind == "condition" for f in facts)
    assert missed > len(pairs) * 0.8


def test_evaluation_reports_each_hard_case(tmp_path):
    generate_all(tmp_path, n_docs=120, seed=2, hard_fraction=0.5)
    ingest(tmp_path, HashingEmbedder())
    report = run_evaluation(RuleExtractor(), "rules", tmp_path)
    assert {"narrative", "family_history", "discontinued_medication"} <= set(report.by_case)
    assert report.by_case["narrative"].recall < 0.5
    assert report.extraction["condition"].recall < 1.0
    assert "| narrative |" in format_report(report)
