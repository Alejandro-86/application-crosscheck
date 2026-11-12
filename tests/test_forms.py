import random

from crosscheck.applications import generate_applications
from crosscheck.forms import render_form
from crosscheck.pharmacy import generate_pharmacy
from crosscheck.synthetic import generate_corpus


def build(seed):
    patients, documents = generate_corpus(150, seed)
    apps = generate_applications(patients, documents, generate_pharmacy(patients, seed), seed)
    by_id = {p.patient_id: p for p in patients}
    rng = random.Random(seed + 3)
    return apps, [render_form(a, by_id[a.patient_id], rng) for a in apps]


def test_forms_are_deterministic_and_carry_the_identifiers():
    apps, forms = build(11)
    assert forms == build(11)[1]
    for app, form in zip(apps, forms, strict=True):
        assert f"Reference: {app.app_id}" in form
        assert f"Patient ID (MRN): {app.mrn}" in form
        assert f"Full name: {app.name}" in form


def test_forms_never_leak_the_answer_key():
    _, forms = build(11)
    for form in forms:
        assert "planted" not in form.lower()
        assert "left out" not in form and "differs from the record" not in form


def test_forms_vary_in_layout_and_wording():
    _, forms = build(11)
    joined = "\n".join(forms)
    assert any("    - " in f or "    * " in f for f in forms)
    assert any("    1. " in f for f in forms)
    assert any("conditions you have been diagnosed with: " in f and "    -" not in f for f in forms)
    assert "HTN" in joined and "Glucophage" in joined
