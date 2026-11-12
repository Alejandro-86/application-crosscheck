from crosscheck.applications import Application, DeclaredMedication
from crosscheck.corpus import LoadedDocument
from crosscheck.embeddings import HashingEmbedder
from crosscheck.pipeline import check_application, check_application_rag
from crosscheck.retrieval import (
    build_index,
    chunk_note,
    queries_for,
    rank,
    reduce_documents,
)
from crosscheck.rules_extractor import RuleExtractor

NOTE = (
    "CLINIC NOTE\n"
    "Patient: Test Patient (MRN 111111), 60-year-old male\n"
    "Date: 2025-03-03\n"
    "Assessment: HTN; asthma.\n"
    "Allergies: latex.\n"
    "No history of OA.\n"
    "Medication review: Glucophage 1000 mg twice daily.\n"
)


def doc(doc_id="D1", text=NOTE):
    return LoadedDocument(doc_id, text, "P1", "clinic_note", [], [])


def app(**overrides):
    base = dict(
        app_id="A1",
        patient_id="P1",
        name="Test Patient",
        mrn="111111",
        declared_conditions=["asthma"],
        declared_medications=[DeclaredMedication("metformin", 500, "mg")],
        declared_allergies=[],
    )
    base.update(overrides)
    return Application(**base)


def test_one_chunk_per_non_empty_line_with_stable_positions():
    chunks = chunk_note("D1", "A\n\n  B  \nC\n")
    assert [(c.idx, c.text) for c in chunks] == [(0, "A"), (1, "B"), (2, "C")]


def test_queries_cover_topics_and_every_declared_item():
    queries = queries_for(app(declared_allergies=["latex"]))
    assert len(queries) > 4
    assert {"asthma", "metformin 500 mg", "latex"} <= set(queries)


def test_a_declared_item_retrieves_the_line_that_names_it_under_another_form():
    embedder = HashingEmbedder()
    index = build_index([doc()], embedder)
    hits = rank(index, embedder, ["metformin 500 mg"], k=1)
    assert [h.chunk.text for h in hits] == ["Medication review: Glucophage 1000 mg twice daily."]
    assert hits[0].queries == ["metformin 500 mg"]


def test_irrelevant_chunks_with_zero_score_are_never_returned():
    embedder = HashingEmbedder()
    index = build_index([doc()], embedder)
    assert rank(index, embedder, ["zzzz qqqq"], k=5) == []
    assert rank([], embedder, ["asthma"]) == []


def test_hits_come_back_in_document_order_and_reduce_keeps_only_those_lines():
    embedder = HashingEmbedder()
    docs = [doc("D1"), doc("D2", "Unrelated letter about parking.\nKind regards.\n")]
    hits = rank(build_index(docs, embedder), embedder, queries_for(app()), k=2)
    assert hits == sorted(hits, key=lambda h: (h.chunk.doc_id, h.chunk.idx))
    reduced = reduce_documents(docs, hits)
    assert [d.doc_id for d in reduced] == ["D1"]
    assert all(line in NOTE for line in reduced[0].text.splitlines())
    assert len(reduced[0].text) < len(NOTE)


def test_rag_check_finds_the_same_flags_as_reading_everything():
    application = app(declared_conditions=["asthma", "osteoarthritis"])
    full = check_application(application, [doc()], [], RuleExtractor())
    rag = check_application_rag(application, [doc()], [], RuleExtractor(), HashingEmbedder(), k=3)
    assert {f.key for f in rag.flags} == {f.key for f in full.flags}
    assert rag.retrieval is not None
    assert 0 < rag.retrieval.chars_sent <= rag.retrieval.chars_total
    assert rag.retrieval.embedder.startswith("hashing")


def test_a_too_small_k_loses_findings_that_compete_for_the_same_slot():
    application = app(declared_conditions=[], declared_medications=[])
    docs = [doc("D1"), doc("D2", "Allergies: aspirin.\n")]
    full = check_application(application, docs, [], RuleExtractor())
    tiny = check_application_rag(application, docs, [], RuleExtractor(), HashingEmbedder(), k=1)
    wide = check_application_rag(application, docs, [], RuleExtractor(), HashingEmbedder(), k=3)
    assert {f.field for f in full.flags} >= {"latex", "aspirin"}
    assert "aspirin" not in {f.field for f in tiny.flags}
    assert {f.key for f in wide.flags} == {f.key for f in full.flags}
    assert tiny.retrieval.chars_sent < tiny.retrieval.chars_total
