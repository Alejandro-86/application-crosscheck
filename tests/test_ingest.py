import sqlite3

import pytest

from crosscheck.corpus import load_applications
from crosscheck.embeddings import HashingEmbedder
from crosscheck.generate import generate_all
from crosscheck.ingest import FormError, ingest, parse_application
from crosscheck.store import StoreDB, load_parsed_applications, store_path

BULLETED = """LIFE INSURANCE APPLICATION (FORM LI-7)
Reference: A0900
Date received: 2025-07-01

SECTION 1 - APPLICANT
Full name: Test Person
Patient ID (MRN): 555123
Age: 47    Sex: Male

SECTION 2 - MEDICAL HISTORY
2a. Medical conditions you have been diagnosed with:
    - HTN
    - type 2 diabetes mellitus
2b. Medications you currently take (name, dose, how often):
    1. Glucophage 500mg twice daily
    2. lisinopril 10 MG once daily
2c. Allergies:
    * penicillin

SECTION 3 - ADDITIONAL INFORMATION
I have never smoked, and I take no HTN medication I forgot to list.
"""

INLINE = """Reference: A0901
Full name: Inline Person
Patient ID (MRN): 555124
2a. Medical conditions: asthma, COPD
2b. Medications you currently take: None
2c. Allergies: NKDA
"""


def items(parsed, kind):
    return [(i.raw, i.canonical, i.dose) for i in parsed.items if i.kind == kind]


def test_bulleted_form_is_parsed_with_canonical_terms_and_doses():
    parsed = parse_application(BULLETED)
    assert (parsed.app_id, parsed.name, parsed.mrn, parsed.age, parsed.sex) == (
        "A0900",
        "Test Person",
        "555123",
        47,
        "M",
    )
    assert parsed.received == "2025-07-01"
    assert items(parsed, "condition") == [
        ("HTN", "hypertension", None),
        ("type 2 diabetes mellitus", "type 2 diabetes mellitus", None),
    ]
    assert items(parsed, "medication") == [
        ("Glucophage 500mg twice daily", "metformin", 500),
        ("lisinopril 10 MG once daily", "lisinopril", 10),
    ]
    assert items(parsed, "allergy") == [("penicillin", "penicillin", None)]
    assert parsed.warnings == []


def test_free_text_outside_section_two_is_never_read_as_a_declaration():
    parsed = parse_application(BULLETED)
    assert "never smoked" not in " ".join(i.raw for i in parsed.items)
    assert len(parsed.items) == 5


def test_inline_layout_and_none_markers():
    parsed = parse_application(INLINE)
    assert [i[1] for i in items(parsed, "condition")] == [
        "asthma",
        "chronic obstructive pulmonary disease",
    ]
    assert items(parsed, "medication") == []
    assert items(parsed, "allergy") == []
    assert parsed.age is None and parsed.sex is None


def test_unknown_terms_are_kept_as_written_and_warned_about_not_guessed():
    text = INLINE.replace("asthma, COPD", "flarbitis").replace(
        "2b. Medications you currently take: None",
        "2b. Medications: mysterypil 5 mg daily; metformin 0.5 g daily; metformin 2.5 mg; ???",
    )
    parsed = parse_application(text)
    assert items(parsed, "condition") == [("flarbitis", None, None)]
    assert [m.canonical for m in parsed.items if m.kind == "medication"] == [None] * 4
    assert len(parsed.warnings) == 5
    assert any("flarbitis" in w for w in parsed.warnings)


@pytest.mark.parametrize(
    ("remove", "expected"),
    [
        ("Reference: A0901\n", "Reference"),
        ("Full name: Inline Person\n", "Full name"),
        ("Patient ID (MRN): 555124\n", "Patient ID"),
        ("2c. Allergies: NKDA\n", "section 2c"),
    ],
)
def test_a_form_missing_a_required_field_is_rejected(remove, expected):
    with pytest.raises(FormError, match=expected):
        parse_application(INLINE.replace(remove, ""))


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("ingest-data")
    generate_all(out, n_docs=60, seed=7)
    return out


def fresh_copy(data_dir, tmp_path):
    import shutil

    target = tmp_path / "data"
    shutil.copytree(data_dir, target)
    store_path(target).unlink(missing_ok=True)
    return target


def test_every_generated_form_round_trips_through_the_scraper(data_dir, tmp_path):
    target = fresh_copy(data_dir, tmp_path)
    report = ingest(target, HashingEmbedder())
    truth = {a.app_id: a for a in load_applications(target)}
    parsed = {a.app_id: a for a in load_parsed_applications(target)}
    assert report.failed == 0 and report.warnings == 0
    assert set(parsed) == set(truth)
    for app_id, expected in truth.items():
        got = parsed[app_id]
        assert (got.name, got.mrn) == (expected.name, expected.mrn)
        assert set(got.declared_conditions) == set(expected.declared_conditions)
        assert set(got.declared_allergies) == set(expected.declared_allergies)
        assert {(m.name, m.dose, m.unit) for m in got.declared_medications} == {
            (m.name, m.dose, m.unit) for m in expected.declared_medications
        }
        assert got.planted_errors == expected.planted_errors


def test_second_run_changes_nothing_then_edits_and_deletions_are_picked_up(data_dir, tmp_path):
    target = fresh_copy(data_dir, tmp_path)
    first = ingest(target, HashingEmbedder())
    assert first.added == len(list((target / "applications").glob("*.txt"))) + len(
        list((target / "docs").glob("*.txt"))
    )
    again = ingest(target, HashingEmbedder())
    assert again.unchanged == first.added and again.added == again.updated == 0
    assert again.chunks_embedded == 0

    form = target / "applications" / "A0001.txt"
    form.write_text(form.read_text().replace("Full name:", "Full name: Renamed "), encoding="utf-8")
    (target / "applications" / "A0002.txt").unlink()
    changed = ingest(target, HashingEmbedder())
    assert (changed.updated, changed.removed, changed.added) == (1, 1, 0)
    db = StoreDB(store_path(target))
    assert db.application("A0001").name.startswith("Renamed")
    assert db.application("A0002") is None


def test_a_broken_form_is_logged_and_not_loaded(data_dir, tmp_path):
    target = fresh_copy(data_dir, tmp_path)
    (target / "applications" / "A0001.txt").write_text("not a form at all\n", encoding="utf-8")
    report = ingest(target, HashingEmbedder())
    db = StoreDB(store_path(target))
    assert report.failed == 1 and db.application("A0001") is None
    assert db.issues()[0]["severity"] == "error"
    assert db.issues()[0]["source"] == "applications/A0001.txt"
    assert ingest(target, HashingEmbedder()).failed == 0  # unchanged, not retried or recounted


def test_duplicate_references_and_notes_without_an_mrn_are_rejected(data_dir, tmp_path):
    target = fresh_copy(data_dir, tmp_path)
    original = (target / "applications" / "A0001.txt").read_text()
    (target / "applications" / "copy.txt").write_text(original, encoding="utf-8")
    (target / "docs" / "stray.txt").write_text("A note with no identifier.\n", encoding="utf-8")
    report = ingest(target, HashingEmbedder())
    messages = " ".join(i["message"] for i in StoreDB(store_path(target)).issues())
    assert report.failed == 2
    assert "duplicate reference A0001" in messages and "no MRN" in messages


def test_changing_the_embedder_re_embeds_every_chunk(data_dir, tmp_path):
    target = fresh_copy(data_dir, tmp_path)
    first = ingest(target, HashingEmbedder(dim=64))
    second = ingest(target, HashingEmbedder(dim=128))
    assert second.chunks_embedded == first.chunks_embedded > 0
    db = StoreDB(store_path(target))
    assert db.meta()["embedder"] == "hashing-v2-128"
    mrn = db.applications()[0].mrn
    assert all(len(vector) == 128 for _, vector in db.index_for_mrn(mrn))


def test_a_failure_while_embedding_leaves_the_store_untouched(data_dir, tmp_path):
    class Exploding:
        name = "boom"

        def embed(self, texts):
            raise RuntimeError("spend cap")

    target = fresh_copy(data_dir, tmp_path)
    ingest(target, HashingEmbedder())
    before = StoreDB(store_path(target)).counts()
    new_note = (target / "docs" / "D0001.txt").read_text().replace("Date:", "Seen on:")
    (target / "docs" / "D0001.txt").write_text(new_note, encoding="utf-8")
    with pytest.raises(RuntimeError):
        ingest(target, Exploding())
    assert StoreDB(store_path(target)).counts() == before
    assert StoreDB(store_path(target)).meta()["embedder"].startswith("hashing")
    assert ingest(target, HashingEmbedder()).updated == 1


def test_the_app_connection_is_read_only_and_queries_are_parameterised(data_dir, tmp_path):
    target = fresh_copy(data_dir, tmp_path)
    ingest(target, HashingEmbedder())
    db = StoreDB(store_path(target))
    with db._connect() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM applications")
    assert db.notes_for_mrn("1' OR '1'='1") == []
    assert db.index_for_mrn("1'; DROP TABLE chunks; --") == []
    assert db.counts()["applications"] > 10


def test_deleting_a_source_cascades_to_its_rows(data_dir, tmp_path):
    target = fresh_copy(data_dir, tmp_path)
    ingest(target, HashingEmbedder())
    (target / "docs" / "D0001.txt").unlink()
    (target / "applications" / "A0001.txt").unlink()
    ingest(target, HashingEmbedder())
    with sqlite3.connect(store_path(target)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id = 'D0001'").fetchone()[0] == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM declared_items WHERE app_id = 'A0001'").fetchone()[0]
            == 0
        )


def test_missing_store_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        StoreDB(tmp_path / "nope.db")
