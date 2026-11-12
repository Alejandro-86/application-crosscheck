"""A mock pharmacy dispensing database: the second source to cross-check against.

It is deliberately imperfect: some medications were never dispensed here (filled elsewhere) and a
few doses differ from the clinical notes (stale or changed prescriptions), so the two sources can
genuinely disagree.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta

from crosscheck.synthetic import MED_CATALOG, Patient


@dataclass
class PharmacyRow:
    mrn: str
    drug: str
    dose: int
    unit: str
    last_dispensed: str

    def citation(self) -> str:
        return f"{self.drug} {self.dose} {self.unit} (dispensed {self.last_dispensed})"


def generate_pharmacy(patients: list[Patient], seed: int = 42) -> list[PharmacyRow]:
    rng = random.Random(seed + 1)
    rows: list[PharmacyRow] = []
    for patient in patients:
        for med in patient.medications:
            if rng.random() < 0.12:
                continue
            dose = med.dose
            if rng.random() < 0.08:
                others = [d for d in MED_CATALOG[med.name][1] if d != med.dose]
                if others:
                    dose = rng.choice(others)
            when = date(2025, 1, 1) + timedelta(days=rng.randrange(0, 365))
            rows.append(PharmacyRow(patient.mrn, med.name, dose, med.unit, when.isoformat()))
    return rows
