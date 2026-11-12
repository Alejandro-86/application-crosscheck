from dataclasses import asdict

from crosscheck.applications import ERROR_TYPES, generate_applications
from crosscheck.pharmacy import generate_pharmacy
from crosscheck.synthetic import generate_corpus
from crosscheck.terminology import normalise_condition, normalise_drug


def build(seed=42, n_docs=200):
    patients, docs = generate_corpus(n_docs, seed)
    pharmacy = generate_pharmacy(patients, seed)
    apps = generate_applications(patients, docs, pharmacy, seed)
    return patients, docs, pharmacy, apps


def test_pharmacy_and_applications_are_deterministic():
    _, _, pharm_a, apps_a = build()
    _, _, pharm_b, apps_b = build()
    assert [asdict(r) for r in pharm_a] == [asdict(r) for r in pharm_b]
    assert [asdict(a) for a in apps_a] == [asdict(a) for a in apps_b]


def test_one_application_per_patient_with_a_mix_of_clean_and_flawed():
    patients, _, _, apps = build()
    assert len(apps) == len(patients)
    flawed = [a for a in apps if a.planted_errors]
    assert 0.35 * len(apps) < len(flawed) < 0.85 * len(apps)
    assert {e.type for a in apps for e in a.planted_errors} == set(ERROR_TYPES)


def test_clean_applications_match_the_record():
    patients, _, _, apps = build()
    by_id = {p.patient_id: p for p in patients}
    for app in apps:
        if app.planted_errors:
            continue
        truth = by_id[app.patient_id]
        assert app.declared_conditions == truth.conditions
        assert sorted(m.name for m in app.declared_medications) == sorted(
            m.name for m in truth.medications
        )
        assert app.declared_allergies == truth.allergies


def test_pharmacy_is_imperfect_but_close():
    patients, _, pharmacy, _ = build()
    n_meds = sum(len(p.medications) for p in patients)
    assert 0.7 * n_meds < len(pharmacy) < n_meds


def test_terminology_maps_abbreviations_and_brands():
    assert normalise_condition("HTN.") == "hypertension"
    assert normalise_condition("T2DM") == "type 2 diabetes mellitus"
    assert normalise_condition("something odd") is None
    assert normalise_drug("Glucophage") == "metformin"
    assert normalise_drug("metformin") == "metformin"
    assert normalise_drug("unknownol") is None
