"""Deterministic comparison of an application against the sources.

No model is involved here. The model only extracts facts; these rules decide what to flag. Every
discrepancy must carry evidence (a quote from a note or a pharmacy row); "unverified" flags say
that nothing was found, which is not the same as a contradiction.
"""

from dataclasses import dataclass, field

from crosscheck.applications import Application
from crosscheck.facts import Evidence, PatientFacts
from crosscheck.pharmacy import PharmacyRow

SEVERITY = {
    "undisclosed_condition": "discrepancy",
    "contradicted_declaration": "discrepancy",
    "undisclosed_medication": "discrepancy",
    "dose_mismatch": "discrepancy",
    "undisclosed_allergy": "discrepancy",
    "source_conflict": "data_quality",
    "unverified_declaration": "unverified",
    "unverified_medication": "unverified",
}


@dataclass
class Flag:
    type: str
    field: str
    message: str
    evidence: list[Evidence] = field(default_factory=list)
    declared: str | None = None
    checked_docs: list[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return SEVERITY[self.type]

    @property
    def key(self) -> tuple[str, str]:
        return (self.type, self.field)


def pharmacy_evidence(row: PharmacyRow) -> Evidence:
    return Evidence("pharmacy", None, row.citation())


def compare(app: Application, facts: PatientFacts, pharmacy_rows: list[PharmacyRow]) -> list[Flag]:
    flags: list[Flag] = []
    flags += _compare_conditions(app, facts)
    flags += _compare_medications(app, facts, pharmacy_rows)
    flags += _compare_allergies(app, facts)
    return sorted(flags, key=lambda f: f.key)


def _compare_conditions(app: Application, facts: PatientFacts) -> list[Flag]:
    flags = []
    declared = set(app.declared_conditions)
    for condition in sorted(set(facts.conditions) - declared):
        flags.append(
            Flag(
                "undisclosed_condition",
                condition,
                f"The record documents {condition}, which the application does not declare.",
                facts.conditions[condition][:1],
            )
        )
    for condition in sorted(declared):
        if condition in facts.conditions:
            continue
        if condition in facts.negated:
            flags.append(
                Flag(
                    "contradicted_declaration",
                    condition,
                    f"The application declares {condition}, but the record explicitly denies it.",
                    facts.negated[condition][:1],
                    declared=condition,
                )
            )
        else:
            flags.append(
                Flag(
                    "unverified_declaration",
                    condition,
                    f"The application declares {condition}; it was not found in the notes "
                    "(absence is not proof).",
                    declared=condition,
                    checked_docs=facts.doc_ids,
                )
            )
    return flags


def _compare_medications(
    app: Application, facts: PatientFacts, pharmacy_rows: list[PharmacyRow]
) -> list[Flag]:
    flags = []
    declared = {m.name: m for m in app.declared_medications}
    pharmacy = {row.drug: row for row in pharmacy_rows}
    for drug in sorted(set(declared) | set(facts.medications) | set(pharmacy)):
        notes = facts.medications.get(drug, [])
        note_doses = {dose for dose, _ in notes if dose is not None}
        note_evidence = [ev for _, ev in notes][:1]
        row = pharmacy.get(drug)
        pharm_evidence = [pharmacy_evidence(row)] if row else []
        all_doses = note_doses | ({row.dose} if row else set())

        if drug in declared:
            dose = declared[drug].dose
            label = f"{drug} {dose} {declared[drug].unit}"
            if not notes and not row:
                flags.append(
                    Flag(
                        "unverified_medication",
                        drug,
                        f"The application declares {label}; no source mentions it.",
                        declared=label,
                        checked_docs=facts.doc_ids,
                    )
                )
            elif all_doses and dose not in all_doses:
                flags.append(
                    Flag(
                        "dose_mismatch",
                        drug,
                        f"The application declares {label}, but the sources show "
                        f"{sorted(all_doses)}.",
                        note_evidence + pharm_evidence,
                        declared=label,
                    )
                )
        else:
            flags.append(
                Flag(
                    "undisclosed_medication",
                    drug,
                    f"{drug} appears in the sources but is not declared in the application.",
                    note_evidence + pharm_evidence,
                )
            )

        if note_doses and row and row.dose not in note_doses:
            flags.append(
                Flag(
                    "source_conflict",
                    drug,
                    f"The notes show {sorted(note_doses)} but the pharmacy shows {row.dose}.",
                    note_evidence + pharm_evidence,
                )
            )
    return flags


def _compare_allergies(app: Application, facts: PatientFacts) -> list[Flag]:
    declared = set(app.declared_allergies)
    return [
        Flag(
            "undisclosed_allergy",
            allergy,
            f"The record documents an allergy to {allergy}, which is not declared.",
            facts.allergies[allergy][:1],
        )
        for allergy in sorted(set(facts.allergies) - declared)
    ]


def ungrounded_flags(
    flags: list[Flag], doc_texts: dict[str, str], pharmacy_rows: list[PharmacyRow]
) -> list[Flag]:
    """Discrepancy and data-quality flags whose evidence cannot be found in the sources."""
    citations = {row.citation() for row in pharmacy_rows}
    bad = []
    for flag in flags:
        if flag.severity == "unverified":
            continue
        grounded = bool(flag.evidence)
        for evidence in flag.evidence:
            if evidence.source == "notes":
                text = doc_texts.get(evidence.doc_id or "", "")
                grounded &= (
                    " ".join(evidence.quote.split()).lower() in " ".join(text.split()).lower()
                )
            else:
                grounded &= evidence.quote in citations
        if not grounded:
            bad.append(flag)
    return bad
