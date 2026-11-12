from dataclasses import asdict

from clinical_rag_lab.synthetic import generate_corpus, write_corpus


def test_same_seed_is_identical():
    a_patients, a_docs = generate_corpus(60, seed=7)
    b_patients, b_docs = generate_corpus(60, seed=7)
    assert [asdict(p) for p in a_patients] == [asdict(p) for p in b_patients]
    assert [asdict(d) for d in a_docs] == [asdict(d) for d in b_docs]


def test_different_seeds_differ():
    _, a_docs = generate_corpus(60, seed=1)
    _, b_docs = generate_corpus(60, seed=2)
    assert [d.text for d in a_docs] != [d.text for d in b_docs]


def test_written_files_are_byte_identical(tmp_path):
    for name in ("a", "b"):
        patients, docs = generate_corpus(40, seed=3)
        write_corpus(patients, docs, tmp_path / name)
    for rel in ("labels.json", "patients.json", "docs/D0001.txt", "docs/D0040.txt"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()


def test_counts_and_references():
    patients, docs = generate_corpus(200, seed=42)
    assert len(docs) == 200
    assert len({d.doc_id for d in docs}) == 200
    patient_ids = {p.patient_id for p in patients}
    assert all(d.patient_id in patient_ids for d in docs)
    assert len({p.mrn for p in patients}) == len(patients)


def test_every_surface_form_appears_in_text():
    _, docs = generate_corpus(200, seed=42)
    for doc in docs:
        for form in doc.surface_forms:
            assert form in doc.text, (doc.doc_id, form)


def test_negated_conditions_are_never_diagnoses():
    patients, _ = generate_corpus(200, seed=42)
    for patient in patients:
        assert not set(patient.negated_conditions) & set(patient.conditions)


def test_hard_cases_are_present():
    _, docs = generate_corpus(200, seed=42)
    seen = {case for d in docs for case in d.hard_cases}
    assert {"negation", "abbreviation", "dose_change", "similar_patient", "brand_name"} <= seen
