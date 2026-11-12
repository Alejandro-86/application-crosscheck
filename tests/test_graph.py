import pytest

pytest.importorskip("langgraph")

from crosscheck.applications import Application, DeclaredMedication  # noqa: E402
from crosscheck.corpus import LoadedDocument  # noqa: E402
from crosscheck.demo_helpers import load_demo_data  # noqa: E402
from crosscheck.embeddings import HashingEmbedder  # noqa: E402
from crosscheck.generate import generate_all  # noqa: E402
from crosscheck.graph import CheckWorkflow, RunContext  # noqa: E402
from crosscheck.pipeline import check_application, check_application_rag  # noqa: E402
from crosscheck.rules_extractor import RuleExtractor  # noqa: E402

NOTE = (
    "CLINIC NOTE\n"
    "Patient: Test Patient (MRN 111111), 60-year-old male\n"
    "Assessment: asthma.\n"
    "Allergies: latex.\n"
)


class CountingExtractor:
    def __init__(self):
        self.calls = 0
        self._inner = RuleExtractor()

    def extract(self, doc_id, text):
        self.calls += 1
        return self._inner.extract(doc_id, text)


def doc(text=NOTE):
    return LoadedDocument("D1", text, "P1", "note", [], [])


def app(conditions=("asthma",), allergies=("latex",)):
    return Application("A1", "P1", "Test", "111111", list(conditions), [], list(allergies))


def ctx_for(application, docs, extractor=None, embedder=None, k=3):
    return RunContext(application, docs, [], extractor or RuleExtractor(), embedder, k)


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("graph-data")
    generate_all(out, n_docs=80, seed=6, hard_fraction=0.3)
    return load_demo_data(out)


def keys(flags):
    return {f.key for f in flags}


def test_the_graph_finds_the_same_flags_as_the_plain_pipeline(data):
    workflow = CheckWorkflow()
    for application in data.applications[:25]:
        docs = data.docs_by_mrn.get(application.mrn, [])
        rows = data.pharmacy_by_mrn.get(application.mrn, [])
        expected = check_application(application, docs, rows, RuleExtractor())
        ctx = RunContext(application, docs, rows, RuleExtractor())
        outcome = workflow.start(ctx, thread_id=application.app_id)
        assert keys(outcome.result.flags) == keys(expected.flags)
        assert outcome.state["dropped"] == len(expected.dropped)


def test_the_retrieval_branch_matches_the_plain_rag_pipeline(data):
    workflow = CheckWorkflow()
    embedder = HashingEmbedder()
    application = data.applications[1]
    docs = data.docs_by_mrn.get(application.mrn, [])
    rows = data.pharmacy_by_mrn.get(application.mrn, [])
    expected = check_application_rag(application, docs, rows, RuleExtractor(), embedder, k=2)
    ctx = RunContext(application, docs, rows, RuleExtractor(), embedder, k=2)
    outcome = workflow.start(ctx, thread_id="rag")
    assert outcome.trace[:3] == ["retrieve", "extract", "compare"]
    assert keys(outcome.result.flags) == keys(expected.flags)
    assert (
        outcome.state["chars_sent"] == expected.retrieval.chars_sent < outcome.state["chars_total"]
    )


def test_without_an_embedder_the_retrieve_step_is_skipped():
    outcome = CheckWorkflow().start(ctx_for(app(), [doc()]), "t")
    assert outcome.trace[:2] == ["extract", "compare"] and "retrieve" not in outcome.trace


def test_a_clean_application_finishes_without_a_review():
    outcome = CheckWorkflow().start(ctx_for(app(), [doc()]), "clean")
    assert outcome.status == "auto_clear"
    assert outcome.trace == ["extract", "compare", "finalize"]
    assert outcome.review_request is None and outcome.state["flags"] == []


def test_flags_pause_the_run_and_resume_does_not_redo_the_extraction():
    extractor = CountingExtractor()
    workflow = CheckWorkflow()
    ctx = ctx_for(app(conditions=(), allergies=()), [doc()], extractor)
    paused = workflow.start(ctx, "t1")
    assert paused.status == "awaiting_review"
    assert extractor.calls == 1 and paused.trace[-1] == "human_review"
    request = paused.review_request
    assert {f["type"] for f in request["flags"]} == {"undisclosed_condition", "undisclosed_allergy"}

    ids = [f["id"] for f in request["flags"]]
    done = workflow.resume(ctx, "t1", {ids[0]: "Confirm", ids[1]: "Dismiss"})
    assert done.status == "reviewed"
    assert done.state["decisions"] == {ids[0]: "Confirm", ids[1]: "Dismiss"}
    assert extractor.calls == 1  # the work before the pause was not repeated
    assert done.trace.count("extract") == 1 and done.trace.count("human_review") == 1


def test_notes_the_system_could_not_read_force_a_review_even_with_no_flags():
    text = NOTE.replace("Assessment: asthma.", "Assessment: asthma; flarbitis.")
    outcome = CheckWorkflow().start(ctx_for(app(), [doc(text)]), "unread")
    assert outcome.state["flags"] == [] and outcome.state["unmapped"] == ["flarbitis"]
    assert outcome.status == "awaiting_review"
    assert outcome.review_request["unmapped"] == ["flarbitis"]


def test_invalid_decisions_are_rejected_and_nothing_is_recorded():
    workflow = CheckWorkflow()
    ctx = ctx_for(app(conditions=()), [doc()])
    paused = workflow.start(ctx, "bad")
    flag_id = paused.review_request["flags"][0]["id"]
    with pytest.raises(ValueError):
        workflow.resume(ctx, "bad", {"A1|made_up|flag": "Confirm"})
    with pytest.raises(ValueError):
        workflow.resume(ctx, "bad", {flag_id: "Maybe"})


def test_resuming_with_no_decisions_marks_the_run_reviewed():
    workflow = CheckWorkflow()
    ctx = ctx_for(app(conditions=()), [doc()])
    workflow.start(ctx, "none")
    done = workflow.resume(ctx, "none", {})
    assert done.status == "reviewed" and done.state["decisions"] == {}


def test_runs_are_isolated_by_thread():
    workflow = CheckWorkflow()
    a = ctx_for(app(conditions=()), [doc()])
    b = ctx_for(app(), [doc()])
    assert workflow.start(a, "a").status == "awaiting_review"
    assert workflow.start(b, "b").status == "auto_clear"
    request = workflow.resume(a, "a", {}).state
    assert request["status"] == "reviewed"


def test_medication_flags_route_the_same_way():
    application = Application(
        "A2",
        "P2",
        "T",
        "111111",
        ["asthma"],
        [DeclaredMedication("metformin", 500, "mg")],
        ["latex"],
    )
    outcome = CheckWorkflow().start(ctx_for(application, [doc()]), "med")
    assert outcome.status == "awaiting_review"
    assert {f["type"] for f in outcome.state["flags"]} == {"unverified_medication"}


def test_notes_that_yield_no_facts_are_never_waved_through_as_clear():
    unreadable = "Unrelated letter about parking.\nKind regards.\n"
    outcome = CheckWorkflow().start(
        ctx_for(app(conditions=(), allergies=()), [doc(unreadable)]), "unread-notes"
    )
    assert outcome.state["flags"] == [] and outcome.state["unmapped"] == []
    assert outcome.state["facts_read"] == 0 and outcome.state["notes_available"] == 1
    assert outcome.status == "awaiting_review"
    assert outcome.review_request["reasons"] == ["no_facts_read"]


def test_no_notes_at_all_is_not_a_coverage_failure():
    outcome = CheckWorkflow().start(ctx_for(app(conditions=(), allergies=()), []), "no-notes")
    assert outcome.status == "auto_clear" and outcome.state["review_reasons"] == []


def test_review_reasons_are_all_listed_and_the_request_carries_them():
    text = NOTE.replace("Assessment: asthma.", "Assessment: asthma; flarbitis.")
    outcome = CheckWorkflow().start(ctx_for(app(conditions=(), allergies=()), [doc(text)]), "all")
    assert outcome.review_request["reasons"] == ["flags", "unmapped_terms"]


def test_retrieval_that_finds_nothing_in_existing_notes_goes_to_review():
    embedder = HashingEmbedder()
    docs = [doc("Zzzz qqqq.\n")]  # no word the queries share
    ctx = ctx_for(app(conditions=(), allergies=()), docs, embedder=embedder, k=1)
    outcome = CheckWorkflow().start(ctx, "nothing-retrieved")
    assert outcome.state["facts_read"] == 0
    assert outcome.review_request["reasons"] == ["no_facts_read"]


def test_a_normally_read_application_still_clears():
    outcome = CheckWorkflow().start(ctx_for(app(), [doc()]), "ok")
    assert outcome.status == "auto_clear" and outcome.state["facts_read"] > 0
