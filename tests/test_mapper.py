import json

import pytest

from crosscheck.applications import Application
from crosscheck.embeddings import HashingEmbedder
from crosscheck.evaluate import format_report, run_evaluation
from crosscheck.generate import generate_all
from crosscheck.ingest import ingest
from crosscheck.llm_extractor import LLMExtractor
from crosscheck.mapper import ConceptMapper
from crosscheck.pipeline import check_application
from crosscheck.rules_extractor import RuleExtractor

NOTE = (
    "CLINIC NOTE\n"
    "Patient: Test Patient (MRN 111111), 60-year-old male\n"
    "The patient has a long-standing history of sugar diabetes and recurrent severe headaches.\n"
    "Allergies: latex.\n"
)
LINE = "The patient has a long-standing history of sugar diabetes and recurrent severe headaches."


class Chat:
    """Answers the extraction request, then each mapping request from a script."""

    def __init__(self, extraction, answers):
        self.extraction = extraction
        self.answers = dict(answers)
        self.mapping_calls = []

    def complete(self, messages, **kwargs):
        if "fixed list of canonical names" in messages[0]["content"]:
            term = messages[1]["content"].split('Term: "')[1].split('"')[0]
            self.mapping_calls.append(term)
            return type("R", (), {"text": self.answers[term]})()
        return type("R", (), {"text": json.dumps(self.extraction)})()


EXTRACTION = {
    "conditions": [
        {"text": "sugar diabetes", "quote": LINE},
        {"text": "recurrent severe headaches", "quote": LINE},
    ],
    "negated_conditions": [],
    "medications": [],
    "allergies": [{"text": "latex", "quote": "Allergies: latex."}],
}


def conditions(result):
    return {f.value for f in result.facts if f.kind == "condition"}


def test_without_a_mapper_unknown_terms_are_reported_not_guessed():
    result = LLMExtractor(Chat(EXTRACTION, {})).extract("D1", NOTE)
    assert conditions(result) == set()
    assert sorted(result.unmapped) == ["recurrent severe headaches", "sugar diabetes"]


def test_the_mapper_picks_from_the_closed_list_and_marks_the_fact_as_model_mapped():
    chat = Chat(
        EXTRACTION,
        {"sugar diabetes": "type 2 diabetes mellitus", "recurrent severe headaches": "migraine"},
    )
    result = LLMExtractor(chat, mapper=ConceptMapper(chat)).extract("D1", NOTE)
    assert conditions(result) == {"type 2 diabetes mellitus", "migraine"}
    assert result.unmapped == []
    assert all(f.via_model for f in result.facts if f.kind == "condition")
    assert not any(f.via_model for f in result.facts if f.kind == "allergy")
    assert result.remapped == [
        "sugar diabetes -> type 2 diabetes mellitus",
        "recurrent severe headaches -> migraine",
    ]
    assert all(f.quote == LINE for f in result.facts if f.kind == "condition")


@pytest.mark.parametrize(
    "answer",
    ["none", "None.", "diabetes", "type 1 diabetes", "I think migraine", "", "asthma, copd"],
)
def test_anything_that_is_not_exactly_a_list_entry_is_treated_as_no_mapping(answer):
    chat = Chat(EXTRACTION, {"sugar diabetes": answer, "recurrent severe headaches": answer})
    result = LLMExtractor(chat, mapper=ConceptMapper(chat)).extract("D1", NOTE)
    assert result.facts and not any(f.kind == "condition" for f in result.facts)
    assert len(result.unmapped) == 2 and result.remapped == []


def test_answer_formatting_noise_is_tolerated_but_the_choice_must_still_be_in_the_list():
    chat = Chat(
        EXTRACTION,
        {"sugar diabetes": '"Type 2 Diabetes Mellitus".', "recurrent severe headaches": "none"},
    )
    result = LLMExtractor(chat, mapper=ConceptMapper(chat)).extract("D1", NOTE)
    assert conditions(result) == {"type 2 diabetes mellitus"}


def test_each_distinct_term_is_asked_about_once_per_run():
    chat = Chat(EXTRACTION, {"sugar diabetes": "none", "recurrent severe headaches": "none"})
    extractor = LLMExtractor(chat, mapper=ConceptMapper(chat))
    extractor.extract("D1", NOTE)
    extractor.extract("D2", NOTE)
    assert sorted(chat.mapping_calls) == ["recurrent severe headaches", "sugar diabetes"]


def test_known_terms_never_reach_the_model():
    known = {
        "conditions": [{"text": "HTN", "quote": "Diagnoses: HTN."}],
        "negated_conditions": [],
        "medications": [],
        "allergies": [],
    }
    chat = Chat(known, {})
    LLMExtractor(chat, mapper=ConceptMapper(chat)).extract("D1", "Diagnoses: HTN.\n")
    assert chat.mapping_calls == []


def test_a_mapped_fact_still_needs_a_grounded_quote_to_produce_a_flag():
    chat = Chat(
        EXTRACTION,
        {"sugar diabetes": "type 2 diabetes mellitus", "recurrent severe headaches": "migraine"},
    )
    app = Application("A1", "P1", "Test", "111111", [], [], [])
    from crosscheck.corpus import LoadedDocument

    doc = LoadedDocument("D1", NOTE, "P1", "note", [], [])
    result = check_application(app, [doc], [], LLMExtractor(chat, mapper=ConceptMapper(chat)))
    flagged = {(f.type, f.field) for f in result.flags}
    assert ("undisclosed_condition", "migraine") in flagged
    assert all(
        f.evidence and f.evidence[0].quote == LINE for f in result.flags if f.field == "migraine"
    )


def test_evaluation_report_counts_model_mapped_terms(tmp_path):
    generate_all(tmp_path, n_docs=60, seed=4, hard_fraction=0.5)
    ingest(tmp_path, HashingEmbedder())
    report = run_evaluation(RuleExtractor(), "rules", tmp_path, limit=5)
    assert report.remapped == 0
    assert "Terms mapped by the model fallback" in format_report(report)
