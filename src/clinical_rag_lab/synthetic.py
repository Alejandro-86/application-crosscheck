"""Deterministic synthetic clinical-style documents with ground-truth labels.

Everything is generated from the word lists below and a seeded RNG. No real patient data and no
data from any employer is used. The same seed always produces identical output.
"""

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

DOC_TYPES = ("discharge_summary", "referral_letter", "clinic_note")
MAX_PATIENTS = 600

FIRST_NAMES = [
    "Alice", "Brian", "Carla", "David", "Elena", "Frank", "Grace", "Hassan", "Irene", "Jamal",
    "Karen", "Liam", "Maya", "Nora", "Omar", "Paula", "Quinn", "Rosa", "Samir", "Tara",
    "Umar", "Vera", "Walter", "Xenia", "Yusuf", "Zoe", "Anil", "Bella", "Colin", "Dina",
]  # fmt: skip
SURNAMES = [
    "Adams", "Baker", "Chen", "Dawson", "Evans", "Foster", "Grant", "Hughes", "Iyer", "Jones",
    "Khan", "Lopez", "Morgan", "Nash", "Owen", "Patel", "Quigley", "Reid", "Silva", "Turner",
    "Usher", "Vance", "Walsh", "Young", "Zhang", "Abbott", "Bishop", "Clarke", "Doyle", "Ellis",
]  # fmt: skip

# canonical name -> surface forms (the first is the long form, the rest are abbreviations)
CONDITION_FORMS = {
    "hypertension": ["hypertension", "HTN"],
    "type 2 diabetes mellitus": ["type 2 diabetes mellitus", "T2DM"],
    "asthma": ["asthma"],
    "chronic obstructive pulmonary disease": ["chronic obstructive pulmonary disease", "COPD"],
    "atrial fibrillation": ["atrial fibrillation", "AF"],
    "hypothyroidism": ["hypothyroidism"],
    "osteoarthritis": ["osteoarthritis", "OA"],
    "chronic kidney disease": ["chronic kidney disease", "CKD"],
    "heart failure": ["heart failure", "HF"],
    "migraine": ["migraine"],
    "depression": ["depression"],
    "gastro-oesophageal reflux disease": ["gastro-oesophageal reflux disease", "GORD"],
}
CONDITIONS = list(CONDITION_FORMS)

# generic name -> (brand name, available doses, unit, frequency)
MED_CATALOG = {
    "metformin": ("Glucophage", [500, 850, 1000], "mg", "twice daily"),
    "lisinopril": ("Zestril", [5, 10, 20], "mg", "once daily"),
    "amlodipine": ("Norvasc", [5, 10], "mg", "once daily"),
    "bisoprolol": ("Cardicor", [5, 10], "mg", "once daily"),
    "atorvastatin": ("Lipitor", [10, 20, 40, 80], "mg", "at night"),
    "omeprazole": ("Losec", [20, 40], "mg", "once daily"),
    "sertraline": ("Lustral", [50, 100], "mg", "once daily"),
    "furosemide": ("Lasix", [20, 40, 80], "mg", "in the morning"),
    "sumatriptan": ("Imigran", [50, 100], "mg", "as needed"),
    "paracetamol": ("Panadol", [500, 1000], "mg", "up to four times daily"),
}
CONDITION_MEDS = {
    "hypertension": ["lisinopril", "amlodipine", "bisoprolol"],
    "type 2 diabetes mellitus": ["metformin"],
    "asthma": [],
    "chronic obstructive pulmonary disease": [],
    "atrial fibrillation": ["bisoprolol"],
    "hypothyroidism": [],
    "osteoarthritis": ["paracetamol"],
    "chronic kidney disease": ["amlodipine"],
    "heart failure": ["furosemide", "bisoprolol"],
    "migraine": ["sumatriptan"],
    "depression": ["sertraline"],
    "gastro-oesophageal reflux disease": ["omeprazole"],
}
ALLERGENS = ["penicillin", "sulfonamides", "latex", "nuts", "aspirin"]
NO_ALLERGY_PHRASES = ["No known drug allergies.", "NKDA."]


@dataclass
class Medication:
    name: str
    brand: str
    dose: int
    unit: str
    frequency: str


@dataclass
class DoseChange:
    drug: str
    from_dose: int
    to_dose: int


@dataclass
class Patient:
    patient_id: str
    name: str
    mrn: str
    age: int
    sex: str
    conditions: list[str]
    medications: list[Medication]
    allergies: list[str]
    negated_conditions: list[str]
    dose_change: DoseChange | None
    similar_to: str | None = None


@dataclass
class Document:
    doc_id: str
    patient_id: str
    doc_type: str
    text: str
    fields_present: list[str]
    hard_cases: list[str]
    surface_forms: list[str] = field(default_factory=list)


def _make_patient(
    rng: random.Random,
    index: int,
    used_names: set[str],
    used_mrns: set[str],
    previous: Patient | None,
) -> Patient:
    similar = previous is not None and rng.random() < 0.15
    for _ in range(300):
        first = rng.choice(FIRST_NAMES)
        surname = previous.name.split()[-1] if similar and previous else rng.choice(SURNAMES)
        name = f"{first} {surname}"
        if name not in used_names:
            break
    else:
        raise ValueError("Could not find an unused patient name; lower n_docs.")
    used_names.add(name)

    while True:
        mrn = str(rng.randrange(100000, 999999))
        if mrn not in used_mrns:
            used_mrns.add(mrn)
            break

    if similar and previous:
        others = [c for c in CONDITIONS if c != previous.conditions[0]]
        conditions = [previous.conditions[0], *rng.sample(others, rng.choice([0, 1]))]
    else:
        conditions = rng.sample(CONDITIONS, rng.choice([1, 2, 3]))

    med_names: list[str] = []
    for condition in conditions:
        for med in CONDITION_MEDS[condition]:
            if med not in med_names and rng.random() < 0.8:
                med_names.append(med)
    if "atorvastatin" not in med_names and rng.random() < 0.3:
        med_names.append("atorvastatin")
    medications = []
    for med in med_names:
        brand, doses, unit, frequency = MED_CATALOG[med]
        medications.append(Medication(med, brand, rng.choice(doses), unit, frequency))

    allergies = rng.sample(ALLERGENS, rng.choice([0, 0, 1, 1, 2]))
    candidates = [c for c in CONDITIONS if c not in conditions]
    negated = [rng.choice(candidates)] if rng.random() < 0.7 else []

    dose_change = None
    changeable = [m for m in medications if any(d < m.dose for d in MED_CATALOG[m.name][1])]
    if changeable and rng.random() < 0.3:
        med = rng.choice(changeable)
        lower = [d for d in MED_CATALOG[med.name][1] if d < med.dose]
        dose_change = DoseChange(med.name, rng.choice(lower), med.dose)

    return Patient(
        patient_id=f"P{index + 1:04d}",
        name=name,
        mrn=mrn,
        age=rng.randrange(25, 91),
        sex=rng.choice(["F", "M"]),
        conditions=conditions,
        medications=medications,
        allergies=allergies,
        negated_conditions=negated,
        dose_change=dose_change,
        similar_to=previous.patient_id if similar and previous else None,
    )


def _condition_form(rng: random.Random, canonical: str, surface: list[str], hard: set[str]) -> str:
    forms = CONDITION_FORMS[canonical]
    if len(forms) > 1 and rng.random() < 0.4:
        form = rng.choice(forms[1:])
        hard.add("abbreviation")
    else:
        form = forms[0]
    surface.append(form)
    return form


def _medication_phrase(
    rng: random.Random, med: Medication, surface: list[str], hard: set[str]
) -> str:
    name = med.name
    if rng.random() < 0.25:
        name = med.brand
        hard.add("brand_name")
    surface.extend([name, f"{med.dose} {med.unit}"])
    return f"{name} {med.dose} {med.unit} {med.frequency}"


def _render_document(
    rng: random.Random, patient: Patient, doc_id: str, doc_type: str, base_date: date
) -> Document:
    surface: list[str] = [patient.name, patient.mrn]
    hard: set[str] = set()
    fields: list[str] = ["diagnoses"]
    sex_word = "female" if patient.sex == "F" else "male"
    when = (base_date + timedelta(days=rng.randrange(0, 365))).isoformat()
    header = (
        f"Patient: {patient.name} (MRN {patient.mrn}), {patient.age}-year-old {sex_word}\n"
        f"Date: {when}"
    )
    conditions = [_condition_form(rng, c, surface, hard) for c in patient.conditions]
    diagnoses = "; ".join(conditions)

    def medication_line(prefix: str) -> str:
        fields.append("medications")
        if not patient.medications:
            return f"{prefix}: no regular medications."
        phrases = [_medication_phrase(rng, m, surface, hard) for m in patient.medications]
        return f"{prefix}: " + "; ".join(phrases) + "."

    def allergy_line() -> str:
        fields.append("allergies")
        if not patient.allergies:
            phrase = rng.choice(NO_ALLERGY_PHRASES)
            surface.append(phrase)
            return phrase
        surface.extend(patient.allergies)
        return "Allergies: " + ", ".join(patient.allergies) + "."

    def negation_line() -> str | None:
        if not patient.negated_conditions:
            return None
        fields.append("negated_conditions")
        hard.add("negation")
        form = _condition_form(rng, patient.negated_conditions[0], surface, hard)
        return rng.choice([f"No history of {form}.", f"Denies any history of {form}."])

    lines: list[str]
    if doc_type == "discharge_summary":
        lines = ["DISCHARGE SUMMARY", header, f"Diagnoses: {diagnoses}."]
        lines.append(medication_line("Medications on discharge"))
        change = patient.dose_change
        if change:
            fields.append("dose_change")
            hard.add("dose_change")
            surface.extend([change.drug, f"{change.from_dose} mg", f"{change.to_dose} mg"])
            lines.append(
                f"{change.drug} was increased from {change.from_dose} mg to "
                f"{change.to_dose} mg during admission."
            )
        lines.append(allergy_line())
        neg = negation_line()
        if neg:
            lines.append(neg)
    elif doc_type == "referral_letter":
        lines = ["Dear Colleague,", header, f"I am referring this patient, who has {diagnoses}."]
        lines.append(medication_line("Current medication"))
        if rng.random() < 0.5:
            lines.append(allergy_line())
        lines.append("Kind regards, Dr. Example")
    else:
        lines = ["CLINIC NOTE", header, f"Assessment: {diagnoses}."]
        lines.append(allergy_line())
        neg = negation_line()
        if neg:
            lines.append(neg)
        if rng.random() < 0.5:
            lines.append(medication_line("Medication review"))

    if patient.similar_to:
        hard.add("similar_patient")
    return Document(
        doc_id=doc_id,
        patient_id=patient.patient_id,
        doc_type=doc_type,
        text="\n".join(lines) + "\n",
        fields_present=sorted(set(fields)),
        hard_cases=sorted(hard),
        surface_forms=surface,
    )


def generate_corpus(n_docs: int = 200, seed: int = 42) -> tuple[list[Patient], list[Document]]:
    if n_docs < 1:
        raise ValueError("n_docs must be at least 1")
    rng = random.Random(seed)
    n_patients = max(1, round(n_docs * 0.6))
    if n_patients > MAX_PATIENTS:
        raise ValueError(f"n_docs too large; at most {int(MAX_PATIENTS / 0.6)} supported.")

    patients: list[Patient] = []
    used_names: set[str] = set()
    used_mrns: set[str] = set()
    for i in range(n_patients):
        previous = patients[-1] if patients else None
        patients.append(_make_patient(rng, i, used_names, used_mrns, previous))

    owners = list(range(n_patients)) + [
        rng.randrange(n_patients) for _ in range(n_docs - n_patients)
    ]
    rng.shuffle(owners)
    base_date = date(2025, 1, 1)
    documents = [
        _render_document(rng, patients[owner], f"D{i + 1:04d}", rng.choice(DOC_TYPES), base_date)
        for i, owner in enumerate(owners)
    ]
    return patients, documents


def write_corpus(patients: list[Patient], documents: list[Document], out_dir: Path) -> None:
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for doc in documents:
        (docs_dir / f"{doc.doc_id}.txt").write_text(doc.text, encoding="utf-8")
    meta = {
        d.doc_id: {
            "patient_id": d.patient_id,
            "doc_type": d.doc_type,
            "fields_present": d.fields_present,
            "hard_cases": d.hard_cases,
        }
        for d in documents
    }
    _dump(out_dir / "labels.json", meta)
    _dump(out_dir / "patients.json", [asdict(p) for p in patients])


def _dump(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
