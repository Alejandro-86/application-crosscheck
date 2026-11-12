from crosscheck.applications import Application, DeclaredMedication
from crosscheck.compare import Flag, compare, ungrounded_flags
from crosscheck.facts import Evidence, PatientFacts
from crosscheck.pharmacy import PharmacyRow


def ev(quote, doc="D1"):
    return Evidence("notes", doc, quote)


def app(conditions=(), meds=(), allergies=()):
    return Application(
        "A1",
        "P1",
        "Test Patient",
        "111111",
        list(conditions),
        [DeclaredMedication(n, d, "mg") for n, d in meds],
        list(allergies),
    )


def types(flags):
    return {f.key for f in flags}


def test_clean_application_has_no_flags():
    facts = PatientFacts(
        conditions={"asthma": [ev("Diagnoses: asthma.")]},
        medications={"metformin": [(500, ev("metformin 500 mg"))]},
        doc_ids=["D1"],
    )
    rows = [PharmacyRow("111111", "metformin", 500, "mg", "2025-03-01")]
    assert compare(app(["asthma"], [("metformin", 500)]), facts, rows) == []


def test_undisclosed_condition_and_allergy_and_medication():
    facts = PatientFacts(
        conditions={"asthma": [ev("a")], "migraine": [ev("m")]},
        medications={"sertraline": [(50, ev("s"))]},
        allergies={"latex": [ev("l")]},
        doc_ids=["D1"],
    )
    flags = compare(app(["asthma"]), facts, [])
    assert types(flags) == {
        ("undisclosed_condition", "migraine"),
        ("undisclosed_medication", "sertraline"),
        ("undisclosed_allergy", "latex"),
    }
    assert all(f.evidence for f in flags)


def test_contradicted_vs_unverified_declaration():
    facts = PatientFacts(
        conditions={"asthma": [ev("a")]},
        negated={"migraine": [ev("No history of migraine.")]},
        doc_ids=["D1"],
    )
    flags = compare(app(["asthma", "migraine", "depression"]), facts, [])
    assert types(flags) == {
        ("contradicted_declaration", "migraine"),
        ("unverified_declaration", "depression"),
    }
    unverified = next(f for f in flags if f.type == "unverified_declaration")
    assert unverified.evidence == [] and unverified.severity == "unverified"


def test_dose_mismatch_only_when_declared_matches_no_source():
    facts = PatientFacts(medications={"metformin": [(500, ev("metformin 500 mg"))]}, doc_ids=["D1"])
    rows = [PharmacyRow("111111", "metformin", 850, "mg", "2025-03-01")]
    # declared matches neither source -> dose_mismatch (and the sources conflict)
    flags = compare(app(meds=[("metformin", 1000)]), facts, rows)
    assert ("dose_mismatch", "metformin") in types(flags)
    assert ("source_conflict", "metformin") in types(flags)
    # declared matches the notes -> only the source conflict
    flags = compare(app(meds=[("metformin", 500)]), facts, rows)
    assert types(flags) == {("source_conflict", "metformin")}


def test_unverified_medication_when_no_source_mentions_it():
    flags = compare(app(meds=[("metformin", 500)]), PatientFacts(doc_ids=["D1"]), [])
    assert types(flags) == {("unverified_medication", "metformin")}


def test_ungrounded_flags_are_detected():
    rows = [PharmacyRow("111111", "metformin", 500, "mg", "2025-03-01")]
    good = Flag("undisclosed_condition", "asthma", "m", [ev("Diagnoses: asthma.")])
    fabricated = Flag("undisclosed_condition", "asthma", "m", [ev("Patient has asthma and gout.")])
    no_evidence = Flag("undisclosed_allergy", "latex", "m", [])
    pharm_ok = Flag(
        "undisclosed_medication", "metformin", "m", [Evidence("pharmacy", None, rows[0].citation())]
    )
    pharm_bad = Flag(
        "undisclosed_medication", "metformin", "m", [Evidence("pharmacy", None, "made up")]
    )
    unverified = Flag("unverified_declaration", "gout", "m", [])
    bad = ungrounded_flags(
        [good, fabricated, no_evidence, pharm_ok, pharm_bad, unverified],
        {"D1": "Diagnoses: asthma.\n"},
        rows,
    )
    assert bad == [fabricated, no_evidence, pharm_bad]
