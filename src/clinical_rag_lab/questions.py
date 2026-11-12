"""Template-generated questions with gold answers and gold source documents.

This is a starting set. The interesting questions (multi-hop, ambiguous, adversarial) are yours to
add in Week 1. An empty gold_sources list means no document can answer the question.
"""

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from clinical_rag_lab.synthetic import Document, Patient

QUESTION_COUNTS = {
    "diagnosis": 12,
    "medications": 12,
    "allergy": 10,
    "dose": 10,
    "negation": 10,
    "unanswerable": 6,
}
UNANSWERABLE_TOPICS = ["blood type", "insurance provider", "next of kin", "employer"]


@dataclass
class Question:
    question_id: str
    kind: str
    text: str
    gold_answer: str
    gold_sources: list[str]
    patient_id: str


def _docs_with(documents: list[Document], patient_id: str, field_name: str) -> list[str]:
    return [
        d.doc_id for d in documents if d.patient_id == patient_id and field_name in d.fields_present
    ]


def _who(patient: Patient) -> str:
    return f"{patient.name} (MRN {patient.mrn})"


def generate_questions(
    patients: list[Patient], documents: list[Document], seed: int = 42
) -> list[Question]:
    rng = random.Random(seed)
    candidates: dict[str, list[Question]] = {kind: [] for kind in QUESTION_COUNTS}

    for p in patients:
        who = _who(p)
        sources = _docs_with(documents, p.patient_id, "diagnoses")
        if sources:
            candidates["diagnosis"].append(
                Question("", "diagnosis", f"Which conditions does {who} have?",
                         "; ".join(p.conditions), sources, p.patient_id)
            )  # fmt: skip
        sources = _docs_with(documents, p.patient_id, "medications")
        if sources and p.medications:
            answer = "; ".join(f"{m.name} {m.dose} {m.unit}" for m in p.medications)
            candidates["medications"].append(
                Question("", "medications", f"What medications is {who} taking?",
                         answer, sources, p.patient_id)
            )  # fmt: skip
        sources = _docs_with(documents, p.patient_id, "allergies")
        if sources:
            answer = ", ".join(p.allergies) if p.allergies else "none documented"
            candidates["allergy"].append(
                Question("", "allergy", f"What allergies are documented for {who}?",
                         answer, sources, p.patient_id)
            )  # fmt: skip
        sources = _docs_with(documents, p.patient_id, "dose_change")
        if sources and p.dose_change:
            change = p.dose_change
            candidates["dose"].append(
                Question("", "dose", f"What is the current dose of {change.drug} for {who}?",
                         f"{change.drug} {change.to_dose} mg", sources, p.patient_id)
            )  # fmt: skip
        sources = _docs_with(documents, p.patient_id, "negated_conditions")
        if sources and p.negated_conditions:
            condition = p.negated_conditions[0]
            candidates["negation"].append(
                Question("", "negation", f"Does {who} have a documented history of {condition}?",
                         "No", sources, p.patient_id)
            )  # fmt: skip
        topic = rng.choice(UNANSWERABLE_TOPICS)
        candidates["unanswerable"].append(
            Question("", "unanswerable", f"What is the {topic} of {who}?",
                     "unanswerable", [], p.patient_id)
        )  # fmt: skip

    chosen: list[Question] = []
    for kind, count in QUESTION_COUNTS.items():
        pool = candidates[kind]
        chosen.extend(rng.sample(pool, min(count, len(pool))))
    rng.shuffle(chosen)
    for i, question in enumerate(chosen):
        question.question_id = f"Q{i + 1:03d}"
    return chosen


def write_questions(questions: list[Question], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(q) for q in questions], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
