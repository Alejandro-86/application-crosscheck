"""LLM-based fact extraction with quote grounding and bounded self-correction.

The model only extracts what the note says, with a verbatim quote for each fact. Code then checks
that every quote really occurs in the note (facts with invented quotes are dropped) and maps terms
to canonical names with the terminology table. If the reply is not valid JSON or contains
ungrounded quotes, the model gets a bounded number of retries; after that we keep only what is
grounded and record what was dropped.
"""

import json
import re

from crosscheck.audit import prompt_version
from crosscheck.facts import ExtractionResult, Fact, note_unmapped
from crosscheck.llm import ChatClient
from crosscheck.mapper import ConceptMapper
from crosscheck.terminology import normalise_allergy, normalise_condition, normalise_drug

MAX_RETRIES = 2

SYSTEM_PROMPT = """You extract structured facts from one clinical note. Return ONLY a JSON object
with these keys:
  "conditions": [{"text": ..., "quote": ...}]
      conditions the note says the patient HAS
  "negated_conditions": [{"text": ..., "quote": ...}]
      conditions the note says the patient does NOT have
  "medications": [{"text": ..., "dose_mg": number or null, "quote": ...}]
  "allergies": [{"text": ..., "quote": ...}]

Rules:
- Use only what is explicitly written. Do not infer, and do not use outside knowledge.
- "quote" must be copied verbatim from the note (a short span that contains the fact).
- "text" is the term as written in the note (keep abbreviations and brand names as written).
- For medications, dose_mg is the dose the patient is CURRENTLY prescribed. If the note says a dose
  was changed, use the final dose, not the earlier one.
- Statements such as "no known drug allergies" produce an empty allergies list.
- If a list has no entries, return an empty list."""

PROMPT_VERSION = prompt_version(SYSTEM_PROMPT)

LISTS = {
    "conditions": "condition",
    "negated_conditions": "negated_condition",
    "medications": "medication",
    "allergies": "allergy",
}


def parse_json(reply: str) -> dict | None:
    text = reply.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _squash(text: str) -> str:
    return " ".join(text.split()).lower()


class LLMExtractor:
    def __init__(
        self,
        chat: ChatClient,
        max_retries: int = MAX_RETRIES,
        mapper: ConceptMapper | None = None,
    ):
        self._chat = chat
        self._max_retries = max_retries
        self._mapper = mapper

    def extract(self, doc_id: str, text: str) -> ExtractionResult:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Note:\n{text}"},
        ]
        best = ExtractionResult(doc_id=doc_id, attempts=0)
        for attempt in range(1, self._max_retries + 2):
            # No temperature is sent: some models accept only their default. The response cache
            # keeps repeated runs on the same input identical.
            reply = self._chat.complete(messages).text
            data = parse_json(reply)
            if data is None:
                problem = "Your reply was not a single valid JSON object. Return only the JSON."
                best.attempts = attempt
            else:
                result = self._build(doc_id, text, data)
                result.attempts = attempt
                best = result
                if not result.ungrounded:
                    return result
                bad = "; ".join(result.ungrounded)
                problem = (
                    f"These quotes do not appear verbatim in the note: {bad}. Fix them or "
                    "remove those facts, and return only the JSON."
                )
            messages = [*messages, {"role": "assistant", "content": reply}]
            messages.append({"role": "user", "content": problem})
        return best

    def _build(self, doc_id: str, text: str, data: dict) -> ExtractionResult:
        result = ExtractionResult(doc_id=doc_id)
        haystack = _squash(text)
        for key, kind in LISTS.items():
            for item in data.get(key) or []:
                if not isinstance(item, dict):
                    continue
                raw = str(item.get("text", "")).strip()
                quote = str(item.get("quote", "")).strip()
                if not raw or not quote or _squash(quote) not in haystack:
                    result.ungrounded.append(quote or raw or "(empty)")
                    continue
                fact = _to_fact(kind, raw, quote, doc_id, item)
                if fact is None and self._mapper and kind != "allergy":
                    mapped = self._mapper.map(_MAP_KIND[kind], raw, quote)
                    if mapped:
                        fact = _to_fact(kind, raw, quote, doc_id, item, canonical=mapped)
                        result.remapped.append(f"{raw} -> {mapped}")
                if fact is None:
                    note_unmapped(result, _MAP_KIND.get(kind, kind), raw)
                else:
                    result.facts.append(fact)
        return result


_MAP_KIND = {
    "condition": "condition",
    "negated_condition": "condition",
    "medication": "medication",
}


def _to_fact(
    kind: str, raw: str, quote: str, doc_id: str, item: dict, canonical: str | None = None
) -> Fact | None:
    if canonical is not None:
        value = canonical
    elif kind in ("condition", "negated_condition"):
        value = normalise_condition(raw)
    elif kind == "medication":
        value = normalise_drug(raw.split()[0]) if raw.split() else None
    else:
        value = normalise_allergy(raw)
    if value is None:
        return None
    dose = None
    if kind == "medication":
        try:
            dose = int(float(item["dose_mg"])) if item.get("dose_mg") is not None else None
        except (TypeError, ValueError):
            dose = None
    return Fact(
        kind,
        value,
        quote,
        doc_id,
        raw,
        via_model=canonical is not None,
        dose=dose,
        unit="mg" if dose is not None else None,
    )
