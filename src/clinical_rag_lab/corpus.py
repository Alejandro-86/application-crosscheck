"""Load the generated corpus, labels and questions from disk."""

import json
from dataclasses import dataclass
from pathlib import Path


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


def load_questions(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "questions.json").read_text(encoding="utf-8"))
