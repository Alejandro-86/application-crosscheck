"""Render applications as full raw-text application forms, the way a submission would arrive.

The structured Application stays the ground truth (and holds the planted errors). The form is a
lossy, messy rendering of it: abbreviations and brand names, two layouts, assorted bullet styles,
dose spellings and filler text. The ingestion step has to parse it back, which is what makes the
scraper worth testing.
"""

import random
from datetime import date, timedelta

from crosscheck.applications import Application
from crosscheck.synthetic import CONDITION_FORMS, MED_CATALOG, Patient

FORM_TITLE = "LIFE INSURANCE APPLICATION (FORM LI-7)"
NONE_CONDITIONS = ["None", "Nil", "N/A"]
NONE_MEDICATIONS = ["None", "Nil", "No regular medication"]
NONE_ALLERGIES = ["None", "NKDA", "No known allergies", "None known"]
OCCUPATIONS = ["Teacher", "Accountant", "Electrician", "Nurse", "Software developer", "Chef"]
FILLER = [
    "I exercise a couple of times a week and have never smoked.",
    "I drink alcohol occasionally, within the recommended limits.",
    "No recent hospital admissions that I can recall.",
    "I travel abroad once or twice a year for work.",
    "Please contact me by email rather than phone.",
]
BULLETS = ["-", "*", "•"]


def _condition_text(rng: random.Random, canonical: str) -> str:
    forms = CONDITION_FORMS[canonical]
    return rng.choice(forms[1:]) if len(forms) > 1 and rng.random() < 0.4 else forms[0]


def _medication_text(rng: random.Random, name: str, dose: int, unit: str) -> str:
    brand, _doses, _unit, frequency = MED_CATALOG[name]
    shown = brand if rng.random() < 0.25 else name
    spacing = rng.choice(["", " ", " "])
    unit_text = unit.upper() if rng.random() < 0.1 else unit
    return f"{shown} {dose}{spacing}{unit_text} {frequency}"


def _block(
    rng: random.Random, header: str, items: list[str], none_text: list[str], *, inline: bool
) -> list[str]:
    if inline:
        body = "; ".join(items) if items else rng.choice(none_text)
        return [f"{header} {body}"]
    lines = [header]
    if not items:
        return [*lines, f"    {rng.choice(none_text)}"]
    style = rng.choice(["bullet", "numbered"])
    mark = rng.choice(BULLETS)
    for number, item in enumerate(items, start=1):
        prefix = mark if style == "bullet" else f"{number}."
        lines.append(f"    {prefix} {item}")
    return lines


def render_form(app: Application, patient: Patient, rng: random.Random) -> str:
    inline = rng.random() < 0.4
    received = date(2025, 6, 1) + timedelta(days=rng.randrange(0, 180))
    sex_word = "Female" if patient.sex == "F" else "Male"
    conditions = [_condition_text(rng, c) for c in app.declared_conditions]
    medications = [_medication_text(rng, m.name, m.dose, m.unit) for m in app.declared_medications]
    allergies = list(app.declared_allergies)
    cover = rng.choice([100, 150, 250, 400]) * 1000

    lines = [
        FORM_TITLE,
        f"Reference: {app.app_id}",
        f"Date received: {received.isoformat()}",
        "",
        "SECTION 1 - APPLICANT",
        f"Full name: {app.name}",
        f"Patient ID (MRN): {app.mrn}",
        f"Age: {patient.age}    Sex: {sex_word}",
        f"Occupation: {rng.choice(OCCUPATIONS)}",
        f"Cover requested: GBP {cover:,} over {rng.choice([10, 20, 25])} years",
        "",
        "SECTION 2 - MEDICAL HISTORY",
    ]
    lines += _block(
        rng,
        "2a. Medical conditions you have been diagnosed with:",
        conditions,
        NONE_CONDITIONS,
        inline=inline,
    )
    lines += _block(
        rng,
        "2b. Medications you currently take (name, dose, how often):",
        medications,
        NONE_MEDICATIONS,
        inline=inline,
    )
    lines += _block(rng, "2c. Allergies:", allergies, NONE_ALLERGIES, inline=inline)
    lines += [
        "",
        "SECTION 3 - ADDITIONAL INFORMATION",
        rng.choice(FILLER),
        "",
        "SECTION 4 - DECLARATION",
        "I declare that the information above is true and complete to the best of my knowledge.",
        f"Signed: {app.name}    Date: {received.isoformat()}",
    ]
    return "\n".join(lines) + "\n"
