"""Shared data types: extracted facts, evidence and merged per-patient facts."""

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Evidence:
    source: str  # "notes" or "pharmacy"
    doc_id: str | None
    quote: str


@dataclass
class Fact:
    kind: str  # condition | negated_condition | medication | allergy
    value: str  # canonical term
    quote: str
    doc_id: str
    raw: str = ""
    dose: int | None = None
    unit: str | None = None


@dataclass
class ExtractionResult:
    doc_id: str
    facts: list[Fact] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)  # terms with no canonical mapping
    ungrounded: list[str] = field(default_factory=list)  # quotes not found in the text (dropped)
    attempts: int = 1


@dataclass
class PatientFacts:
    conditions: dict[str, list[Evidence]] = field(default_factory=dict)
    negated: dict[str, list[Evidence]] = field(default_factory=dict)
    medications: dict[str, list[tuple[int | None, Evidence]]] = field(default_factory=dict)
    allergies: dict[str, list[Evidence]] = field(default_factory=dict)
    doc_ids: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)


def merge_results(results: list[ExtractionResult]) -> PatientFacts:
    conditions: dict[str, list[Evidence]] = defaultdict(list)
    negated: dict[str, list[Evidence]] = defaultdict(list)
    medications: dict[str, list[tuple[int | None, Evidence]]] = defaultdict(list)
    allergies: dict[str, list[Evidence]] = defaultdict(list)
    unmapped: list[str] = []
    for result in results:
        unmapped.extend(result.unmapped)
        for fact in result.facts:
            evidence = Evidence("notes", fact.doc_id, fact.quote)
            if fact.kind == "condition":
                conditions[fact.value].append(evidence)
            elif fact.kind == "negated_condition":
                negated[fact.value].append(evidence)
            elif fact.kind == "medication":
                medications[fact.value].append((fact.dose, evidence))
            elif fact.kind == "allergy":
                allergies[fact.value].append(evidence)
    return PatientFacts(
        conditions=dict(conditions),
        negated=dict(negated),
        medications=dict(medications),
        allergies=dict(allergies),
        doc_ids=sorted({r.doc_id for r in results}),
        unmapped=sorted(set(unmapped)),
    )
