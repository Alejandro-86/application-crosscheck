"""Ground truth derived from the generator's labels, used for expected flags and extraction scoring.

The oracle is "what a perfect extractor would have found in these notes": a patient fact counts
only if at least one of the patient's documents actually states that kind of information.
"""

from crosscheck.facts import ExtractionResult, Fact, PatientFacts, merge_results
from crosscheck.synthetic import Patient

TRUTH_QUOTE = "(ground truth)"


def doc_truth(patient: Patient, fields_present: list[str]) -> set[tuple]:
    """Facts a perfect extractor would find in one document: (kind, value, dose)."""
    truth: set[tuple] = set()
    if "diagnoses" in fields_present:
        truth |= {("condition", c, None) for c in patient.conditions}
    if "negated_conditions" in fields_present:
        truth |= {("negated_condition", c, None) for c in patient.negated_conditions}
    if "medications" in fields_present:
        truth |= {("medication", m.name, m.dose) for m in patient.medications}
    if "allergies" in fields_present:
        truth |= {("allergy", a, None) for a in patient.allergies}
    return truth


def oracle_results(patient: Patient, docs: list) -> list[ExtractionResult]:
    results = []
    for doc in docs:
        facts = [
            Fact(kind, value, TRUTH_QUOTE, doc.doc_id, dose=dose)
            for kind, value, dose in sorted(
                doc_truth(patient, doc.fields_present), key=lambda t: (t[0], t[1])
            )
        ]
        results.append(ExtractionResult(doc.doc_id, facts))
    return results


def oracle_facts(patient: Patient, docs: list) -> PatientFacts:
    return merge_results(oracle_results(patient, docs))
