"""A rule-based extractor for the synthetic notes: a free, deterministic baseline.

It works because the synthetic notes use a small set of templates. Real notes would defeat it,
which is the point of comparing it against the LLM extractor on the same evaluation.
"""

import re

from crosscheck.facts import ExtractionResult, Fact
from crosscheck.terminology import normalise_allergy, normalise_condition, normalise_drug

CONDITIONS_LINE = re.compile(r"^(?:Diagnoses|Assessment): (?P<body>.+)\.$")
REFERRAL_LINE = re.compile(r"who has (?P<body>.+)\.$")
MEDS_LINE = re.compile(
    r"^(?:Medications on discharge|Current medication|Medication review): (?P<body>.+)\.$"
)
MED_ITEM = re.compile(r"^(?P<name>[A-Za-z]+) (?P<dose>\d+) (?P<unit>mg)\b")
ALLERGY_LINE = re.compile(r"^Allergies: (?P<body>.+)\.$")
NEGATION_LINE = re.compile(r"^(?:No history of|Denies any history of) (?P<cond>.+)\.$")


class RuleExtractor:
    def extract(self, doc_id: str, text: str) -> ExtractionResult:
        result = ExtractionResult(doc_id=doc_id)
        for line in text.splitlines():
            line = line.strip()
            if match := (CONDITIONS_LINE.match(line) or REFERRAL_LINE.search(line)):
                for item in match["body"].split(";"):
                    self._add_condition(result, "condition", item, line)
            elif match := MEDS_LINE.match(line):
                for item in match["body"].split(";"):
                    self._add_medication(result, item.strip(), line)
            elif match := ALLERGY_LINE.match(line):
                for item in match["body"].split(","):
                    value = normalise_allergy(item)
                    result.facts.append(Fact("allergy", value, line, doc_id, item.strip()))
            elif match := NEGATION_LINE.match(line):
                self._add_condition(result, "negated_condition", match["cond"], line)
        for fact in result.facts:
            fact.doc_id = doc_id
        return result

    @staticmethod
    def _add_condition(result: ExtractionResult, kind: str, item: str, line: str) -> None:
        item = item.strip()
        value = normalise_condition(item)
        if value is None:
            result.unmapped.append(item)
        else:
            result.facts.append(Fact(kind, value, line, result.doc_id, item))

    @staticmethod
    def _add_medication(result: ExtractionResult, item: str, line: str) -> None:
        match = MED_ITEM.match(item)
        if not match:
            return
        drug = normalise_drug(match["name"])
        if drug is None:
            result.unmapped.append(match["name"])
            return
        result.facts.append(
            Fact(
                "medication",
                drug,
                line,
                result.doc_id,
                item,
                dose=int(match["dose"]),
                unit=match["unit"],
            )
        )
