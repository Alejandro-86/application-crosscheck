"""The cross-check as a LangGraph workflow with a human-review step.

    START -> [retrieve] -> extract -> compare -> route -+-> human_review (pauses) -> finalize
                                                        +-> finalize (nothing to review)

The pipeline functions are the same ones `pipeline.check_application` uses, so the graph returns
the same flags. What the graph adds is control flow a plain function does not give you:
  - a conditional entry (retrieve only when an embedder is supplied),
  - a routing rule: review when there are flags, OR the notes contained terms the system could not
    read, OR notes exist but the extractor read no facts from any of them. "Clear" must mean the
    sources were read and agree, never "nothing was found",
  - a real pause: `human_review` interrupts, the state is checkpointed, and the run resumes with
    the reviewer's decisions without redoing the extraction.

Heavy objects (extractor, embedder, documents) travel in the run config, not in the checkpointed
state, which stays small and serialisable. Needs `pip install langgraph`.
"""

from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from crosscheck.applications import Application
from crosscheck.compare import compare, ungrounded_flags
from crosscheck.corpus import LoadedDocument
from crosscheck.demo_helpers import flag_id
from crosscheck.embeddings import Embedder
from crosscheck.facts import merge_results
from crosscheck.pharmacy import PharmacyRow
from crosscheck.pipeline import CheckResult, Extractor
from crosscheck.retrieval import (
    DEFAULT_K,
    Index,
    RetrievalInfo,
    build_index,
    queries_for,
    rank,
    reduce_documents,
)

DECISIONS = ("Confirm", "Dismiss", "—")


class CheckState(TypedDict, total=False):
    app_id: str
    flags: list[dict]
    unmapped: list[str]
    facts_read: int
    notes_available: int
    review_reasons: list[str]
    dropped: int
    chars_sent: int
    chars_total: int
    route: str
    decisions: dict[str, str]
    status: str


@dataclass
class RunContext:
    """Everything a run needs that should not be checkpointed, plus what the nodes produce."""

    app: Application
    docs: list[LoadedDocument]
    pharmacy_rows: list[PharmacyRow]
    extractor: Extractor
    embedder: Embedder | None = None
    k: int = DEFAULT_K
    index: Index | None = None
    used_docs: list[LoadedDocument] = field(default_factory=list)
    retrieval: RetrievalInfo | None = None
    results: list = field(default_factory=list)
    result: CheckResult | None = None
    trace: list[str] = field(default_factory=list)


def _ctx(config) -> RunContext:
    return config["configurable"]["run"]


def _enter(state: CheckState, config) -> str:
    return "retrieve" if _ctx(config).embedder is not None else "extract"


def retrieve(state: CheckState, config) -> CheckState:
    ctx = _ctx(config)
    ctx.trace.append("retrieve")
    assert ctx.embedder is not None
    index = ctx.index if ctx.index is not None else build_index(ctx.docs, ctx.embedder)
    queries = queries_for(ctx.app)
    hits = rank(index, ctx.embedder, queries, ctx.k)
    ctx.used_docs = reduce_documents(ctx.docs, hits)
    ctx.retrieval = RetrievalInfo(
        embedder=ctx.embedder.name,
        k=ctx.k,
        queries=queries,
        hits=hits,
        chars_total=sum(len(d.text) for d in ctx.docs),
        chars_sent=sum(len(d.text) for d in ctx.used_docs),
    )
    return {"chars_sent": ctx.retrieval.chars_sent, "chars_total": ctx.retrieval.chars_total}


def extract(state: CheckState, config) -> CheckState:
    ctx = _ctx(config)
    ctx.trace.append("extract")
    if ctx.embedder is None:
        ctx.used_docs = ctx.docs
    ctx.results = [ctx.extractor.extract(d.doc_id, d.text) for d in ctx.used_docs]
    return {}


def compare_and_ground(state: CheckState, config) -> CheckState:
    ctx = _ctx(config)
    ctx.trace.append("compare")
    facts = merge_results(ctx.results)
    flags = compare(ctx.app, facts, ctx.pharmacy_rows)
    dropped = ungrounded_flags(flags, {d.doc_id: d.text for d in ctx.used_docs}, ctx.pharmacy_rows)
    kept = [f for f in flags if f not in dropped]
    ctx.result = CheckResult(ctx.app.app_id, kept, facts, dropped, ctx.results, ctx.retrieval)
    facts_read = sum(len(r.facts) for r in ctx.results)
    reasons = []
    if kept:
        reasons.append("flags")
    if facts.unmapped:
        reasons.append("unmapped_terms")
    if ctx.docs and facts_read == 0:
        reasons.append("no_facts_read")
    return {
        "flags": [
            {
                "id": flag_id(ctx.app.app_id, f),
                "type": f.type,
                "field": f.field,
                "severity": f.severity,
            }
            for f in kept
        ],
        "unmapped": facts.unmapped,
        "facts_read": facts_read,
        "notes_available": len(ctx.docs),
        "review_reasons": reasons,
        "dropped": len(dropped),
    }


def route(state: CheckState) -> str:
    """Review whenever there is something to look at or the sources were not really read."""
    return "human_review" if state.get("review_reasons") else "finalize"


def _validate_decisions(decisions: dict[str, str], shown: set[str]) -> None:
    for key, value in decisions.items():
        if key not in shown or value not in DECISIONS:
            raise ValueError(f"Invalid review decision: {key!r} -> {value!r}")


def human_review(state: CheckState, config) -> CheckState:
    ctx = _ctx(config)
    if not ctx.trace or ctx.trace[-1] != "human_review":  # this node re-enters on resume
        ctx.trace.append("human_review")
    reply = interrupt(
        {
            "app_id": state["app_id"],
            "reasons": state["review_reasons"],
            "flags": state["flags"],
            "unmapped": state["unmapped"],
        }
    )
    decisions = reply["decisions"]  # wrapped: LangGraph ignores an empty resume value
    _validate_decisions(decisions, {f["id"] for f in state["flags"]})
    return {"decisions": {k: v for k, v in decisions.items() if v != "—"}}


def finalize(state: CheckState, config) -> CheckState:
    _ctx(config).trace.append("finalize")
    return {"status": "reviewed" if "decisions" in state else "auto_clear"}


def build_graph(checkpointer=None):
    graph = StateGraph(CheckState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("extract", extract)
    graph.add_node("compare", compare_and_ground)
    graph.add_node("human_review", human_review)
    graph.add_node("finalize", finalize)
    graph.add_conditional_edges(START, _enter, ["retrieve", "extract"])
    graph.add_edge("retrieve", "extract")
    graph.add_edge("extract", "compare")
    graph.add_conditional_edges("compare", route, ["human_review", "finalize"])
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


@dataclass
class Outcome:
    status: str  # awaiting_review | reviewed | auto_clear
    state: dict[str, Any]
    review_request: dict | None
    result: CheckResult | None
    trace: list[str]


class CheckWorkflow:
    """Start a check, and (if it pauses for review) resume it with the reviewer's decisions."""

    def __init__(self, checkpointer=None):
        self._graph = build_graph(checkpointer)

    @staticmethod
    def _config(ctx: RunContext, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id, "run": ctx}}

    def _outcome(self, ctx: RunContext, thread_id: str, output: dict) -> Outcome:
        config = self._config(ctx, thread_id)
        state = dict(self._graph.get_state(config).values)
        pending = output.get("__interrupt__")
        status = "awaiting_review" if pending else state.get("status", "auto_clear")
        return Outcome(
            status=status,
            state=state,
            review_request=pending[0].value if pending else None,
            result=ctx.result,
            trace=list(ctx.trace),
        )

    def start(self, ctx: RunContext, thread_id: str) -> Outcome:
        output = self._graph.invoke({"app_id": ctx.app.app_id}, self._config(ctx, thread_id))
        return self._outcome(ctx, thread_id, output)

    def resume(self, ctx: RunContext, thread_id: str, decisions: dict[str, str]) -> Outcome:
        # Validate BEFORE resuming: once a resume value is delivered, LangGraph keeps it, so a
        # value rejected inside the node would be replayed on every later attempt.
        shown = {
            f["id"]
            for f in self._graph.get_state(self._config(ctx, thread_id)).values.get("flags", [])
        }
        _validate_decisions(decisions, shown)
        output = self._graph.invoke(
            Command(resume={"decisions": decisions}), self._config(ctx, thread_id)
        )
        return self._outcome(ctx, thread_id, output)


def main() -> None:
    import argparse
    from pathlib import Path

    from crosscheck.demo_helpers import load_demo_data
    from crosscheck.embeddings import HashingEmbedder
    from crosscheck.rules_extractor import RuleExtractor

    parser = argparse.ArgumentParser(description="Run one application through the graph.")
    parser.add_argument("app_id", help="for example A0001")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--rag", action="store_true", help="retrieve passages first")
    parser.add_argument(
        "--decide", choices=["Confirm", "Dismiss"], help="resume with this decision"
    )
    args = parser.parse_args()

    data = load_demo_data(args.data)
    app = next((a for a in data.applications if a.app_id == args.app_id), None)
    if app is None:
        raise SystemExit(f"Unknown application {args.app_id}")
    ctx = RunContext(
        app,
        data.docs_by_mrn.get(app.mrn, []),
        data.pharmacy_by_mrn.get(app.mrn, []),
        RuleExtractor(),
        HashingEmbedder() if args.rag else None,
    )
    workflow = CheckWorkflow()
    outcome = workflow.start(ctx, thread_id=app.app_id)
    print("path:", " -> ".join(outcome.trace), "| status:", outcome.status)
    if outcome.state.get("review_reasons"):
        print("  review because:", ", ".join(outcome.state["review_reasons"]))
    for flag in outcome.state.get("flags", []):
        print(f"  {flag['severity']:<11} {flag['type']} / {flag['field']}")
    if outcome.state.get("unmapped"):
        print("  note terms the system could not read:", "; ".join(outcome.state["unmapped"]))
    if outcome.status == "awaiting_review" and args.decide:
        flags = outcome.review_request["flags"]
        outcome = workflow.resume(ctx, app.app_id, {f["id"]: args.decide for f in flags})
        print("resumed ->", outcome.status, "| decisions:", len(outcome.state["decisions"]))
    elif outcome.status == "awaiting_review":
        print("paused: waiting for a reviewer (use --decide Confirm to resume)")


if __name__ == "__main__":
    main()
