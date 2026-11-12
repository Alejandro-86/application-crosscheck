"""Harder clinical notes: narrative letters with the traps a templated note never contains.

Each hard note states every fact the patient really has (so the labels in `oracle.py` still
apply) but in free text, and adds sentences that must NOT become facts:
  - family_history: a relative's condition ("Mother had asthma").
  - discontinued_medication: a drug the patient used to take.
Other hard cases: paraphrased conditions (some listed in the terminology table as synonyms, some
deliberately not, to exercise the unmapped-term path), doses written in grams, negation phrased
in other ways, and a dose change inside a sentence.
"""

import random
from datetime import date, timedelta

from crosscheck.synthetic import (
    CONDITION_FORMS,
    CONDITION_SYNONYMS,
    CONDITIONS,
    MED_CATALOG,
    NO_ALLERGY_PHRASES,
    UNLISTED_PARAPHRASES,
    Document,
    Patient,
)

RELATIVES = ["mother", "father", "sister", "brother"]


def _condition_phrase(
    rng: random.Random, canonical: str, surface: list[str], hard: set[str]
) -> str:
    roll = rng.random()
    if roll < 0.25 and canonical in UNLISTED_PARAPHRASES:
        phrase = UNLISTED_PARAPHRASES[canonical]
        hard.add("unlisted_paraphrase")
    elif roll < 0.55 and canonical in CONDITION_SYNONYMS:
        phrase = rng.choice(CONDITION_SYNONYMS[canonical])
        hard.add("paraphrase")
    else:
        forms = CONDITION_FORMS[canonical]
        phrase = rng.choice(forms)
    surface.append(phrase)
    return phrase


def _dose_text(rng: random.Random, dose: int, hard: set[str]) -> str:
    if dose in (500, 1000) and rng.random() < 0.6:
        hard.add("dose_in_grams")
        return "0.5 g" if dose == 500 else "1 g"
    return f"{dose} mg"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def render_hard_document(
    rng: random.Random, patient: Patient, doc_id: str, base_date: date
) -> Document:
    surface: list[str] = [patient.name, patient.mrn]
    hard: set[str] = {"narrative"}
    fields: list[str] = ["diagnoses", "medications", "allergies"]
    sex_word = "female" if patient.sex == "F" else "male"
    when = (base_date + timedelta(days=rng.randrange(0, 365))).isoformat()

    conditions = [_condition_phrase(rng, c, surface, hard) for c in patient.conditions]
    history = f"The patient has a long-standing history of {_join(conditions)}."

    if patient.medications:
        phrases = []
        for med in patient.medications:
            name = med.brand if rng.random() < 0.25 else med.name
            if name == med.brand:
                hard.add("brand_name")
            phrases.append(f"{name} {_dose_text(rng, med.dose, hard)} {med.frequency}")
            surface.append(name)
        treatment = f"Current treatment is {_join(phrases)}."
    else:
        treatment = "The patient takes no regular medication."

    sentences = [history, treatment]
    change = patient.dose_change
    if change:
        fields.append("dose_change")
        hard.add("dose_change")
        sentences.append(
            f"The dose of {change.drug} was raised from {change.from_dose} mg to "
            f"{change.to_dose} mg after the last review."
        )

    taken = {m.name for m in patient.medications}
    old_drugs = [m for m in MED_CATALOG if m not in taken]
    if old_drugs and rng.random() < 0.5:
        old = rng.choice(old_drugs)
        hard.add("discontinued_medication")
        sentences.append(f"Previously prescribed {old}; this was stopped last year.")

    others = [c for c in CONDITIONS if c not in patient.conditions]
    if patient.negated_conditions:
        fields.append("negated_conditions")
        hard.add("negation")
        condition = patient.negated_conditions[0]
        long_form = CONDITION_FORMS[condition][0]
        sentences.append(
            rng.choice(
                [
                    f"There is no evidence of {long_form}.",
                    f"{long_form.capitalize()} has been ruled out.",
                ]
            )
        )
        others = [c for c in others if c != condition]
    if others and rng.random() < 0.6:
        hard.add("family_history")
        relative = rng.choice(RELATIVES)
        relative_condition = CONDITION_FORMS[rng.choice(others)][0]
        sentences.append(f"Family history: the patient's {relative} had {relative_condition}.")

    if patient.allergies:
        allergy = f"The patient reacts to {_join(patient.allergies)}."
        surface.extend(patient.allergies)
    else:
        allergy = rng.choice(NO_ALLERGY_PHRASES)
        surface.append(allergy)
    sentences.append(allergy)

    text = "\n".join(
        [
            "Dear Colleague,",
            f"Patient: {patient.name} (MRN {patient.mrn}), {patient.age}-year-old {sex_word}",
            f"Date: {when}",
            "I reviewed this patient today. " + " ".join(sentences),
            "Kind regards, Dr. Example",
        ]
    )
    return Document(
        doc_id=doc_id,
        patient_id=patient.patient_id,
        doc_type="narrative_letter",
        text=text + "\n",
        fields_present=sorted(set(fields)),
        hard_cases=sorted(hard),
        surface_forms=surface,
    )
