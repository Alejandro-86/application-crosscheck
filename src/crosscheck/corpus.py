"""Load the generated corpus, labels and questions from disk."""

import json
from dataclasses import dataclass
from pathlib import Path

from crosscheck.applications import Application, application_from_dict
from crosscheck.pharmacy import PharmacyRow
from crosscheck.synthetic import Patient, patient_from_dict


@dataclass
class LoadedDocument:
    doc_id: str
    text: str
    patient_id: str
    doc_type: str
    fields_present: list[str]
    hard_cases: list[str]


def load_documents(data_dir: Path) -> list[LoadedDocument]:
    labels = json.loads((data_dir / "labels.json").read_text(encoding="utf-8"))
    documents = []
    for doc_id in sorted(labels):
        meta = labels[doc_id]
        text = (data_dir / "docs" / f"{doc_id}.txt").read_text(encoding="utf-8")
        documents.append(
            LoadedDocument(
                doc_id=doc_id,
                text=text,
                patient_id=meta["patient_id"],
                doc_type=meta["doc_type"],
                fields_present=meta["fields_present"],
                hard_cases=meta["hard_cases"],
            )
        )
    return documents


def load_patients(data_dir: Path) -> list[Patient]:
    raw = json.loads((data_dir / "patients.json").read_text(encoding="utf-8"))
    return [patient_from_dict(p) for p in raw]


def load_applications(data_dir: Path) -> list[Application]:
    raw = json.loads((data_dir / "applications.json").read_text(encoding="utf-8"))
    return [application_from_dict(a) for a in raw]


def load_pharmacy(data_dir: Path) -> list[PharmacyRow]:
    raw = json.loads((data_dir / "pharmacy.json").read_text(encoding="utf-8"))
    return [PharmacyRow(**r) for r in raw]
