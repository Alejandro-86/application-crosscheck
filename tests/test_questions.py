from dataclasses import asdict

from clinical_rag_lab.corpus import load_documents, load_questions
from clinical_rag_lab.generate import main
from clinical_rag_lab.questions import generate_questions
from clinical_rag_lab.synthetic import generate_corpus

FIELD_FOR_KIND = {
    "diagnosis": "diagnoses",
    "medications": "medications",
    "allergy": "allergies",
    "dose": "dose_change",
    "negation": "negated_conditions",
}


def test_questions_are_deterministic():
    patients, docs = generate_corpus(200, seed=42)
    a = generate_questions(patients, docs, seed=42)
    b = generate_questions(patients, docs, seed=42)
    assert [asdict(q) for q in a] == [asdict(q) for q in b]


def test_gold_sources_exist_and_contain_the_field():
    patients, docs = generate_corpus(200, seed=42)
    by_id = {d.doc_id: d for d in docs}
    questions = generate_questions(patients, docs, seed=42)
    assert len(questions) >= 40
    for q in questions:
        if q.kind == "unanswerable":
            assert q.gold_sources == []
            continue
        assert q.gold_sources
        for doc_id in q.gold_sources:
            assert FIELD_FOR_KIND[q.kind] in by_id[doc_id].fields_present
            assert by_id[doc_id].patient_id == q.patient_id


def test_every_kind_is_covered():
    patients, docs = generate_corpus(200, seed=42)
    kinds = {q.kind for q in generate_questions(patients, docs, seed=42)}
    assert kinds == {"diagnosis", "medications", "allergy", "dose", "negation", "unanswerable"}


def test_cli_writes_loadable_corpus(tmp_path, monkeypatch):
    out = tmp_path / "gen"
    monkeypatch.setattr(
        "sys.argv", ["generate", "--n-docs", "50", "--seed", "5", "--out", str(out)]
    )
    main()
    docs = load_documents(out)
    questions = load_questions(out)
    assert len(docs) == 50
    assert questions
    assert all(d.text.strip() for d in docs)
