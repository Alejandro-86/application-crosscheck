import random
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from crosscheck.applications import Application, DeclaredMedication
from crosscheck.compare import compare, ungrounded_flags
from crosscheck.config import ConfigError
from crosscheck.corpus import LoadedDocument
from crosscheck.costs import BudgetExceededError
from crosscheck.demo_helpers import load_demo_data
from crosscheck.embeddings import HashingEmbedder
from crosscheck.facts import merge_results
from crosscheck.generate import generate_all
from crosscheck.graph import (
    MAX_PARALLEL,
    CheckWorkflow,
    RunContext,
    build_graph,
    compact_path,
)
from crosscheck.pipeline import check_application, check_application_rag
from crosscheck.retrieval import build_index, queries_for, rank, reduce_documents
from crosscheck.rules_extractor import RuleExtractor

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
        self._lock = threading.Lock()

    def extract(self, doc_id, text):
        with self._lock:
            self.calls += 1
        return self._inner.extract(doc_id, text)


def doc(text=NOTE, doc_id="D1"):
    return LoadedDocument(doc_id, text, "P1", "note", [], [])


def app(conditions=("asthma",), allergies=("latex",)):
    return Application("A1", "P1", "Test", "111111", list(conditions), [], list(allergies))


def ctx_for(application, docs, extractor=None, embedder=None, k=3):
    return RunContext(application, docs, [], extractor or RuleExtractor(), embedder, k)


def reference_check(application, docs, rows, extractor):
    """The original algorithm, written out plainly: the graph must agree with it."""
    results = [extractor.extract(d.doc_id, d.text) for d in docs]
    facts = merge_results(results)
    flags = compare(application, facts, rows)
    dropped = ungrounded_flags(flags, {d.doc_id: d.text for d in docs}, rows)
    return [f for f in flags if f not in dropped], facts


def keys(flags):
    return {f.key for f in flags}


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("graph-data")
    generate_all(out, n_docs=80, seed=6, hard_fraction=0.3)
    return load_demo_data(out)


# --- one engine: the graph agrees with the plain algorithm -------------------------------------


def test_the_graph_agrees_with_the_reference_algorithm(data):
    for application in data.applications[:30]:
        docs = data.docs_by_mrn.get(application.mrn, [])
        rows = data.pharmacy_by_mrn.get(application.mrn, [])
        flags, facts = reference_check(application, docs, rows, RuleExtractor())
        result = check_application(application, docs, rows, RuleExtractor())
        assert keys(result.flags) == keys(flags)
        assert result.facts.conditions.keys() == facts.conditions.keys()
        assert [f.evidence for f in result.flags] == [f.evidence for f in flags]


def test_the_retrieval_branch_agrees_with_retrieve_then_extract(data):
    embedder = HashingEmbedder()
    application = data.applications[1]
    docs = data.docs_by_mrn.get(application.mrn, [])
    rows = data.pharmacy_by_mrn.get(application.mrn, [])
    hits = rank(build_index(docs, embedder), embedder, queries_for(application), 2)
    flags, _ = reference_check(application, reduce_documents(docs, hits), rows, RuleExtractor())
    result = check_application_rag(application, docs, rows, RuleExtractor(), embedder, k=2)
    assert keys(result.flags) == keys(flags)
    assert result.retrieval.chars_sent < result.retrieval.chars_total


# --- parallel extraction (Send) ----------------------------------------------------------------


def many_docs(n):
    return [doc(NOTE, f"D{i}") for i in range(n)]


class SlowExtractor:
    """Takes a random moment per note and records how many ran at once."""

    def __init__(self, seed=1):
        self._inner = RuleExtractor()
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def extract(self, doc_id, text):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            pause = self._rng.uniform(0.01, 0.06)
        time.sleep(pause)
        result = self._inner.extract(doc_id, text)
        with self._lock:
            self.active -= 1
        return result


def test_notes_are_extracted_in_parallel_but_within_the_cap():
    slow = SlowExtractor()
    check_application(app(), many_docs(12), [], slow)
    assert 1 < slow.peak <= MAX_PARALLEL


def test_parallel_results_are_identical_to_sequential_ones_whatever_finishes_first():
    docs = many_docs(10)
    flags, facts = reference_check(app(conditions=()), docs, [], RuleExtractor())
    for seed in range(4):
        result = check_application(app(conditions=()), docs, [], SlowExtractor(seed))
        assert keys(result.flags) == keys(flags)
        assert [f.evidence for f in result.flags] == [f.evidence for f in flags]
        assert result.facts.doc_ids == facts.doc_ids
        assert [r.doc_id for r in result.results] == [d.doc_id for d in docs]


@pytest.mark.parametrize(
    "error", [PermissionError("p"), ConfigError("c"), BudgetExceededError("b")]
)
def test_errors_from_a_parallel_branch_reach_the_caller_unwrapped(error):
    class Failing:
        def extract(self, doc_id, text):
            raise error

    with pytest.raises(type(error)):
        check_application(app(), many_docs(3), [], Failing())
    with pytest.raises(type(error)):
        CheckWorkflow().start(ctx_for(app(), many_docs(3), Failing()), "err")


def test_no_notes_goes_straight_to_compare():
    outcome = CheckWorkflow().start(ctx_for(app(conditions=(), allergies=()), []), "none")
    assert outcome.trace == ["select_notes", "compare", "finalize"]
    assert outcome.status == "auto_clear"


# --- paths, routing and the review pause ------------------------------------------------------


def test_the_path_shows_the_parallel_fan_out_and_the_retrieval_step():
    workflow = CheckWorkflow()
    plain = workflow.start(ctx_for(app(), many_docs(3)), "plain")
    assert compact_path(plain.trace) == ["select_notes", "extract_note x3", "compare", "finalize"]
    rag = workflow.start(ctx_for(app(), many_docs(3), embedder=HashingEmbedder()), "rag")
    assert compact_path(rag.trace)[:2] == ["retrieve", "select_notes"]
    assert sorted(rag.state["docs_read"]) == sorted(set(rag.state["docs_read"]))


def test_a_clean_application_finishes_without_a_review():
    outcome = CheckWorkflow().start(ctx_for(app(), [doc()]), "clean")
    assert outcome.status == "auto_clear" and outcome.review_request is None
    assert outcome.trace == ["select_notes", "extract_note", "compare", "finalize"]


def test_flags_pause_the_run_and_resume_does_not_redo_the_extraction():
    extractor = CountingExtractor()
    workflow = CheckWorkflow()
    ctx = ctx_for(app(conditions=(), allergies=()), many_docs(3), extractor)
    paused = workflow.start(ctx, "t1")
    assert paused.status == "awaiting_review" and extractor.calls == 3
    assert paused.trace[-1] == "compare" and workflow.is_paused("t1")
    request = paused.review_request
    assert request["reasons"] == ["flags"]
    assert {f["type"] for f in request["flags"]} == {"undisclosed_condition", "undisclosed_allergy"}

    ids = [f["id"] for f in request["flags"]]
    done = workflow.resume(ctx, "t1", {ids[0]: "Confirm", ids[1]: "Dismiss"})
    assert done.status == "reviewed" and not workflow.is_paused("t1")
    assert done.state["decisions"] == {ids[0]: "Confirm", ids[1]: "Dismiss"}
    assert extractor.calls == 3
    assert done.trace[-2:] == ["human_review", "finalize"]


def test_the_batch_graph_never_pauses_and_marks_what_would_need_review():
    graph = build_graph(interactive=False)
    ctx = ctx_for(app(conditions=()), [doc()])
    out = graph.invoke({"app_id": "A1"}, {"configurable": {"run": ctx}})
    assert out["status"] == "needs_review" and "decisions" not in out
    clean = ctx_for(app(), [doc()])
    assert (
        graph.invoke({"app_id": "A1"}, {"configurable": {"run": clean}})["status"] == "auto_clear"
    )


def test_notes_the_system_could_not_read_force_a_review_even_with_no_flags():
    text = NOTE.replace("Assessment: asthma.", "Assessment: asthma; flarbitis.")
    outcome = CheckWorkflow().start(ctx_for(app(), [doc(text)]), "unread")
    assert outcome.state["flags"] == [] and outcome.state["unmapped"] == ["flarbitis"]
    assert outcome.status == "awaiting_review"
    assert outcome.review_request["reasons"] == ["unmapped_terms"]


def test_notes_that_yield_no_facts_are_never_waved_through_as_clear():
    unreadable = "Unrelated letter about parking.\nKind regards.\n"
    ctx = ctx_for(app(conditions=(), allergies=()), [doc(unreadable)])
    outcome = CheckWorkflow().start(ctx, "unread-notes")
    assert outcome.state["facts_read"] == 0 and outcome.state["notes_available"] == 1
    assert outcome.review_request["reasons"] == ["no_facts_read"]


def test_no_notes_at_all_is_not_a_coverage_failure():
    outcome = CheckWorkflow().start(ctx_for(app(conditions=(), allergies=()), []), "no-notes")
    assert outcome.status == "auto_clear" and outcome.state["review_reasons"] == []


def test_all_review_reasons_are_listed():
    text = NOTE.replace("Assessment: asthma.", "Assessment: asthma; flarbitis.")
    ctx = ctx_for(app(conditions=(), allergies=()), [doc(text)])
    assert CheckWorkflow().start(ctx, "all").review_request["reasons"] == [
        "flags",
        "unmapped_terms",
    ]


def test_retrieval_that_finds_nothing_in_existing_notes_goes_to_review():
    ctx = ctx_for(
        app(conditions=(), allergies=()), [doc("Zzzz qqqq.\n")], None, HashingEmbedder(), 1
    )
    outcome = CheckWorkflow().start(ctx, "nothing-retrieved")
    assert outcome.state["facts_read"] == 0
    assert outcome.review_request["reasons"] == ["no_facts_read"]


def test_a_rejected_decision_does_not_poison_the_paused_run():
    workflow = CheckWorkflow()
    ctx = ctx_for(app(conditions=()), [doc()])
    paused = workflow.start(ctx, "retry")
    flag = paused.review_request["flags"][0]["id"]
    with pytest.raises(ValueError):
        workflow.resume(ctx, "retry", {"A1|made_up|flag": "Confirm"})
    with pytest.raises(ValueError):
        workflow.resume(ctx, "retry", {flag: "Maybe"})
    done = workflow.resume(ctx, "retry", {flag: "Confirm"})
    assert done.status == "reviewed" and done.state["decisions"] == {flag: "Confirm"}


def test_resuming_a_run_that_is_not_paused_is_an_error():
    workflow = CheckWorkflow()
    with pytest.raises(LookupError):
        workflow.resume(None, "never-started", {})
    workflow.start(ctx_for(app(), [doc()]), "finished")
    with pytest.raises(LookupError):
        workflow.resume(None, "finished", {})


def test_resuming_with_no_decisions_marks_the_run_reviewed_and_threads_are_isolated():
    workflow = CheckWorkflow()
    a = ctx_for(app(conditions=()), [doc()])
    assert workflow.start(a, "a").status == "awaiting_review"
    assert workflow.start(ctx_for(app(), [doc()]), "b").status == "auto_clear"
    done = workflow.resume(a, "a", {})
    assert done.status == "reviewed" and done.state["decisions"] == {}


def test_medication_flags_route_the_same_way():
    declared = [DeclaredMedication("metformin", 500, "mg")]
    application = Application("A2", "P2", "T", "111111", ["asthma"], declared, ["latex"])
    outcome = CheckWorkflow().start(ctx_for(application, [doc()]), "med")
    assert outcome.status == "awaiting_review"
    assert {f["type"] for f in outcome.state["flags"]} == {"unverified_medication"}


# --- durable state, streaming, history and introspection -------------------------------------


def saver(path):
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


def test_a_paused_review_survives_a_restart(tmp_path):
    db = tmp_path / "checkpoints.db"
    first = CheckWorkflow(saver(db))
    paused = first.start(ctx_for(app(conditions=()), [doc()]), "durable")
    assert paused.status == "awaiting_review"
    flag = paused.review_request["flags"][0]["id"]

    # A new process: new saver, new workflow object, no run context from before the pause.
    second = CheckWorkflow(saver(db))
    assert second.is_paused("durable")
    restored = second.outcome(None, "durable")
    assert (
        restored.status == "awaiting_review" and restored.review_request["flags"][0]["id"] == flag
    )
    done = second.resume(ctx_for(app(), []), "durable", {flag: "Confirm"})
    assert done.status == "reviewed" and done.state["decisions"] == {flag: "Confirm"}
    assert not CheckWorkflow(saver(db)).is_paused("durable")


def test_streaming_reports_each_step_and_the_pause():
    workflow = CheckWorkflow()
    steps = list(workflow.stream(ctx_for(app(conditions=()), many_docs(2)), "stream"))
    nodes = [s["node"] for s in steps]
    assert nodes[0] == "select_notes" and nodes.count("extract_note") == 2
    assert nodes[-2:] == ["compare", "human_review"] and steps[-1]["paused"] is True
    assert steps[-1]["request"]["reasons"] == ["flags"]
    assert steps[-2]["detail"]["review_reasons"] == ["flags"] and "path" not in steps[-2]["detail"]


def test_the_state_history_lists_every_checkpoint_newest_first():
    workflow = CheckWorkflow()
    workflow.start(ctx_for(app(conditions=()), many_docs(2)), "hist")
    history = workflow.history("hist")
    assert history[0]["next"] == ["human_review"] and history[0]["flags"] == 1
    assert history[-1]["next"] == ["__start__"] or history[-1]["path"] == []
    steps = [h["step"] for h in history]
    assert steps == sorted(steps, reverse=True) and len(history) >= 5
    assert history[0]["path"] == ["select_notes", "extract_note x2", "compare"]
    assert workflow.history("unknown-thread") == []


def test_the_graph_describes_itself_for_the_diagram():
    described = CheckWorkflow().describe()
    assert {
        "retrieve",
        "select_notes",
        "extract_note",
        "compare",
        "human_review",
        "finalize",
    } <= set(described["nodes"])
    edges = {(e["source"], e["target"]): e["conditional"] for e in described["edges"]}
    assert edges[("select_notes", "extract_note")] is True  # the Send fan-out
    assert edges[("extract_note", "compare")] is False
    assert edges[("human_review", "finalize")] is False
    assert "human_review" in described["mermaid"] and described["mermaid"].startswith("---")


def test_compact_path_merges_repeats():
    assert compact_path(["a", "b", "b", "b", "c", "b"]) == ["a", "b x3", "c", "b"]
    assert compact_path([]) == []


def test_the_readme_diagram_names_every_node_in_the_real_graph():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    start = readme.index("```mermaid")
    diagram = readme[start : readme.index("```", start + 10)]
    nodes = set(CheckWorkflow().describe()["nodes"]) - {"__start__", "__end__"}
    assert [n for n in nodes if n not in diagram] == []


def test_every_check_entry_point_goes_through_the_graph(monkeypatch):
    import crosscheck.graph as graph_module

    calls = []
    real = graph_module.run_check
    monkeypatch.setattr(
        graph_module, "run_check", lambda ctx: calls.append(ctx.app.app_id) or real(ctx)
    )
    check_application(app(), [doc()], [], RuleExtractor())
    check_application_rag(app(), [doc()], [], RuleExtractor(), HashingEmbedder())
    assert calls == ["A1", "A1"]
