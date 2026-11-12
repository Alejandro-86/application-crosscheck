import json
from types import SimpleNamespace

from crosscheck.llm_extractor import LLMExtractor, parse_json

NOTE = (
    "DISCHARGE SUMMARY\n"
    "Patient: Test Patient (MRN 111111), 60-year-old male\n"
    "Diagnoses: HTN; asthma.\n"
    "Medications on discharge: Glucophage 1000 mg twice daily.\n"
    "metformin was increased from 500 mg to 1000 mg during admission.\n"
    "Allergies: latex.\n"
    "No history of OA.\n"
)

GOOD = {
    "conditions": [
        {"text": "HTN", "quote": "Diagnoses: HTN; asthma."},
        {"text": "asthma", "quote": "Diagnoses: HTN; asthma."},
    ],
    "negated_conditions": [{"text": "OA", "quote": "No history of OA."}],
    "medications": [
        {"text": "Glucophage", "dose_mg": 1000, "quote": "Glucophage 1000 mg twice daily"}
    ],
    "allergies": [{"text": "latex", "quote": "Allergies: latex."}],
}


class ScriptedChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages, **kwargs):
        self.calls.append(messages)
        text = self.replies.pop(0)
        return type("Result", (), {"text": text})()


def facts_of(result):
    return {(f.kind, f.value, f.dose) for f in result.facts}


def test_good_reply_is_normalised_and_grounded():
    chat = ScriptedChat([json.dumps(GOOD)])
    result = LLMExtractor(chat).extract("D1", NOTE)
    assert facts_of(result) == {
        ("condition", "hypertension", None),
        ("condition", "asthma", None),
        ("negated_condition", "osteoarthritis", None),
        ("medication", "metformin", 1000),
        ("allergy", "latex", None),
    }
    assert result.attempts == 1 and not result.ungrounded and not result.unmapped


def test_fenced_json_is_accepted():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json("not json") is None
    assert parse_json("[1, 2]") is None


def test_invented_quote_is_dropped_then_corrected_on_retry():
    bad = {**GOOD, "conditions": [{"text": "gout", "quote": "Patient has gout."}]}
    chat = ScriptedChat([json.dumps(bad), json.dumps(GOOD)])
    result = LLMExtractor(chat).extract("D1", NOTE)
    assert result.attempts == 2
    assert ("condition", "hypertension", None) in facts_of(result)
    assert "gout" not in {f.raw for f in result.facts}
    assert "do not appear verbatim" in chat.calls[1][-1]["content"]


def test_persistent_hallucination_is_dropped_and_recorded():
    bad = {**GOOD, "conditions": [{"text": "gout", "quote": "Patient has gout."}]}
    chat = ScriptedChat([json.dumps(bad)] * 3)
    result = LLMExtractor(chat, max_retries=2).extract("D1", NOTE)
    assert result.attempts == 3
    assert result.ungrounded == ["Patient has gout."]
    assert all(f.raw != "gout" for f in result.facts)


def test_invalid_json_is_retried_then_returns_empty():
    chat = ScriptedChat(["nope", "still nope", "never"])
    result = LLMExtractor(chat, max_retries=2).extract("D1", NOTE)
    assert result.facts == []
    assert result.attempts == 3


def test_unknown_terms_are_reported_not_guessed():
    reply = {
        "conditions": [{"text": "gout", "quote": "Diagnoses: HTN; asthma."}],
        "negated_conditions": [],
        "medications": [{"text": "Mysterypil", "dose_mg": 5, "quote": "Glucophage 1000 mg"}],
        "allergies": [],
    }
    result = LLMExtractor(ScriptedChat([json.dumps(reply)])).extract("D1", NOTE)
    assert result.facts == []
    assert set(result.unmapped) == {"gout", "Mysterypil"}


def test_the_extractor_does_not_send_a_temperature():
    seen = []

    class Chat:
        def complete(self, messages, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(text='{"conditions": []}')

    LLMExtractor(Chat()).extract("D1", "No findings.")
    assert seen == [{}]
