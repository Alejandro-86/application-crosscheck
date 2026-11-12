"""Synthetic insurance applications with deliberately planted errors.

An application starts as a copy of the patient's true record, then some applications get one or
two planted errors. Errors are only planted where the sources contain evidence that could expose
them, so each one is detectable in principle.
"""

import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from crosscheck.pharmacy import PharmacyRow
from crosscheck.synthetic import CONDITIONS, MED_CATALOG, Document, Patient

ERROR_TYPES = (
    "undisclosed_condition",
    "false_declaration",
    "wrong_dose",
    "undisclosed_medication",
    "undisclosed_allergy",
)


@dataclass
class DeclaredMedication:
    name: str
    dose: int
    unit: str


@dataclass
class PlantedError:
    type: str
    field: str
    detail: str


@dataclass
class Application:
    app_id: str
    patient_id: str
    name: str
    mrn: str
    declared_conditions: list[str]
    declared_medications: list[DeclaredMedication]
    declared_allergies: list[str]
    planted_errors: list[PlantedError] = field(default_factory=list)


def application_from_dict(data: dict) -> Application:
    return Application(
        app_id=data["app_id"],
        patient_id=data["patient_id"],
        name=data["name"],
        mrn=data["mrn"],
        declared_conditions=list(data["declared_conditions"]),
        declared_medications=[DeclaredMedication(**m) for m in data["declared_medications"]],
        declared_allergies=list(data["declared_allergies"]),
        planted_errors=[PlantedError(**e) for e in data["planted_errors"]],
    )


def applications_to_dicts(apps: list[Application]) -> list[dict]:
    return [asdict(a) for a in apps]


def generate_applications(
    patients: list[Patient],
    documents: list[Document],
    pharmacy: list[PharmacyRow],
    seed: int = 42,
    error_rate: float = 0.6,
) -> list[Application]:
    rng = random.Random(seed + 2)
    available: dict[str, set[str]] = defaultdict(set)
    for doc in documents:
        available[doc.patient_id].update(doc.fields_present)
    pharmacy_doses: dict[str, dict[str, int]] = defaultdict(dict)
    for row in pharmacy:
        pharmacy_doses[row.mrn][row.drug] = row.dose

    applications = []
    for index, patient in enumerate(patients):
        fields = available[patient.patient_id]
        pharm = pharmacy_doses[patient.mrn]
        declared_conditions = list(patient.conditions)
        declared_meds = {
            m.name: DeclaredMedication(m.name, m.dose, m.unit) for m in patient.medications
        }
        declared_allergies = list(patient.allergies)
        errors: list[PlantedError] = []

        checkable = [m for m in patient.medications if "medications" in fields or m.name in pharm]
        touched: set[str] = set()
        wanted = rng.choice([1, 2]) if rng.random() < error_rate else 0
        for error_type in rng.sample(ERROR_TYPES, len(ERROR_TYPES)):
            if len(errors) >= wanted:
                break
            if error_type == "undisclosed_condition" and len(declared_conditions) >= 2:
                removed = rng.choice(declared_conditions)
                declared_conditions.remove(removed)
                errors.append(PlantedError(error_type, removed, "condition left out"))
            elif error_type == "false_declaration":
                if (
                    patient.negated_conditions
                    and "negated_conditions" in fields
                    and rng.random() < 0.5
                ):
                    condition, detail = patient.negated_conditions[0], "denied in the record"
                else:
                    pool = [
                        c
                        for c in CONDITIONS
                        if c not in patient.conditions and c not in patient.negated_conditions
                    ]
                    condition, detail = rng.choice(pool), "not mentioned in the record"
                declared_conditions.append(condition)
                errors.append(PlantedError(error_type, condition, detail))
            elif error_type == "wrong_dose":
                options = [m for m in checkable if m.name not in touched]
                if not options:
                    continue
                med = rng.choice(options)
                banned = {med.dose, pharm.get(med.name)}
                doses = [d for d in MED_CATALOG[med.name][1] if d not in banned]
                if not doses:
                    continue
                declared_meds[med.name].dose = rng.choice(doses)
                touched.add(med.name)
                errors.append(PlantedError(error_type, med.name, "dose differs from the record"))
            elif error_type == "undisclosed_medication":
                options = [m for m in checkable if m.name not in touched]
                if not options:
                    continue
                med = rng.choice(options)
                del declared_meds[med.name]
                touched.add(med.name)
                errors.append(PlantedError(error_type, med.name, "medication left out"))
            elif (
                error_type == "undisclosed_allergy" and declared_allergies and "allergies" in fields
            ):
                removed = rng.choice(declared_allergies)
                declared_allergies.remove(removed)
                errors.append(PlantedError(error_type, removed, "allergy left out"))

        applications.append(
            Application(
                app_id=f"A{index + 1:04d}",
                patient_id=patient.patient_id,
                name=patient.name,
                mrn=patient.mrn,
                declared_conditions=declared_conditions,
                declared_medications=list(declared_meds.values()),
                declared_allergies=declared_allergies,
                planted_errors=errors,
            )
        )
    return applications
