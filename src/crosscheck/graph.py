"""The cross-check, as a LangGraph workflow. Every check in this project runs through it.

    START -> [retrieve] -> select_notes --Send per note--> extract_note (parallel) -> compare
                                                                                        |
                              +-- human_review (pauses for a person) -> finalize  <-----+
                              +-- finalize (nothing to review)

What LangGraph provides here, and where:
  - Typed state with reducers: `path` and `docs_read` are appended to by parallel branches.
  - Conditional edges: retrieval only when an embedder is supplied; the review routing rule.
  - `Send` (map-reduce): one `extract_note` task per note, run in parallel up to MAX_PARALLEL.
  - `interrupt` / `Command(resume=...)`: the review step pauses the run and resumes it with a
    person's decisions, without repeating the extraction.
  - A SQLite checkpointer: a paused review survives a server restart; the run is resumed by thread.
  - Streaming (`stream_mode="updates"`): node-by-node progress, used by the web page.
  - State history: every checkpoint of a run can be listed afterwards.
  - Graph introspection: the diagram in the web page and the README comes from `get_graph()`.

The same node functions run in two compiled graphs: an interactive one (with the pause, needs a
checkpointer) used by the review workflow, and a batch one (no pause; a run that would need a
review ends with the status "needs_review") used by `pipeline.check_application`, the evaluation
and the regression gate. The routing rule is identical in both.

Heavy objects (extractor, embedder, documents) travel in the run config, not in the checkpointed
state, which stays small and serialisable.
"""

import operator
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from crosscheck.applications import Application
from crosscheck.compare import compare, ungrounded_flags
from crosscheck.corpus import LoadedDocument
from crosscheck.demo_helpers import flag_id
from crosscheck.embeddings import Embedder
from crosscheck.facts import ExtractionResult, merge_results
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
MAX_PARALLEL = 4


class CheckState(TypedDict, total=False):
    app_id: str
    path: Annotated[list[str], operator.add]
    docs_read: Annotated[list[str], operator.add]
    flags: list[dict]
    unmapped: list[str]
    facts_read: int
    notes_available: int
    review_reasons: list[str]
    dropped: int
    chars_sent: int
    chars_total: int
    decisions: dict[str, str]
    status: str


class NoteInput(TypedDict):
    index: int


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
    results: dict[int, ExtractionResult] = field(default_factory=dict)
    result: CheckResult | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def _ctx(config) -> RunContext:
    return config["configurable"]["run"]


def _enter(state: CheckState, config) -> str:
    return "retrieve" if _ctx(config).embedder is not None else "select_notes"


def retrieve(state: CheckState, config) -> CheckState:
    ctx = _ctx(config)
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
    return {
        "path": ["retrieve"],
        "chars_sent": ctx.retrieval.chars_sent,
        "chars_total": ctx.retrieval.chars_total,
    }


def select_notes(state: CheckState, config) -> CheckState:
    ctx = _ctx(config)
    if ctx.embedder is None:
        ctx.used_docs = ctx.docs
    return {"path": ["select_notes"], "notes_available": len(ctx.docs)}


def fan_out(state: CheckState, config) -> list:
    """One extraction task per note, run in parallel; with no notes, go straight to compare."""
    used = _ctx(config).used_docs
    return [Send("extract_note", {"index": i}) for i in range(len(used))] or ["compare"]


def extract_note(state: NoteInput, config) -> CheckState:
    ctx = _ctx(config)
    doc = ctx.used_docs[state["index"]]
    result = ctx.extractor.extract(doc.doc_id, doc.text)
    with ctx.lock:
        ctx.results[state["index"]] = result
    return {"path": ["extract_note"], "docs_read": [doc.doc_id]}


def compare_and_ground(state: CheckState, config) -> CheckState:
    ctx = _ctx(config)
    # Merge in note order, not completion order, so parallel runs give identical results.
    results = [ctx.results[i] for i in sorted(ctx.results)]
    facts = merge_results(results)
    flags = compare(ctx.app, facts, ctx.pharmacy_rows)
    dropped = ungrounded_flags(flags, {d.doc_id: d.text for d in ctx.used_docs}, ctx.pharmacy_rows)
    kept = [f for f in flags if f not in dropped]
    ctx.result = CheckResult(ctx.app.app_id, kept, facts, dropped, results, ctx.retrieval)
    facts_read = sum(len(r.facts) for r in results)
    reasons = []
    if kept:
        reasons.append("flags")
    if facts.unmapped:
        reasons.append("unmapped_terms")
    if ctx.docs and facts_read == 0:
        reasons.append("no_facts_read")
    return {
        "path": ["compare"],
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
        "review_reasons": reasons,
        "dropped": len(dropped),
    }


def _route_interactive(state: CheckState) -> str:
    """Review whenever there is something to look at or the sources were not really read."""
    return "human_review" if state.get("review_reasons") else "finalize"


def _route_batch(state: CheckState) -> str:
    return "finalize"


def _validate_decisions(decisions: dict[str, str], shown: set[str]) -> None:
    for key, value in decisions.items():
        if key not in shown or value not in DECISIONS:
            raise ValueError(f"Invalid review decision: {key!r} -> {value!r}")


def human_review(state: CheckState) -> CheckState:
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
    return {"path": ["human_review"], "decisions": {k: v for k, v in decisions.items() if v != "—"}}


def finalize(state: CheckState) -> CheckState:
    if "decisions" in state:
        status = "reviewed"
    elif state.get("review_reasons"):
        status = "needs_review"
    else:
        status = "auto_clear"
    return {"path": ["finalize"], "status": status}


def build_graph(*, interactive: bool, checkpointer=None):
    graph = StateGraph(CheckState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("select_notes", select_notes)
    graph.add_node("extract_note", extract_note, input_schema=NoteInput)
    graph.add_node("compare", compare_and_ground)
    graph.add_node("finalize", finalize)
    graph.add_conditional_edges(START, _enter, ["retrieve", "select_notes"])
    graph.add_edge("retrieve", "select_notes")
    graph.add_conditional_edges("select_notes", fan_out, ["extract_note", "compare"])
    graph.add_edge("extract_note", "compare")
    if interactive:
        graph.add_node("human_review", human_review)
        graph.add_conditional_edges("compare", _route_interactive, ["human_review", "finalize"])
        graph.add_edge("human_review", "finalize")
    else:
        graph.add_conditional_edges("compare", _route_batch, ["finalize"])
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer if interactive else None)


_batch_graph = None
_batch_lock = threading.Lock()


def run_check(ctx: RunContext) -> CheckResult:
    """Run the graph without a pause, and return the result (used by every non-interactive path)."""
    global _batch_graph
    with _batch_lock:
        if _batch_graph is None:
            _batch_graph = build_graph(interactive=False)
    config = {"configurable": {"run": ctx}, "max_concurrency": MAX_PARALLEL}
    _batch_graph.invoke({"app_id": ctx.app.app_id}, config)
    assert ctx.result is not None
    return ctx.result


def compact_path(path: list[str]) -> list[str]:
    """['select_notes', 'extract_note', 'extract_note'] -> ['select_notes', 'extract_note x2']."""
    out: list[str] = []
    for step in path:
        if out and out[-1].split(" x")[0] == step:
            count = int(out[-1].split(" x")[1]) + 1 if " x" in out[-1] else 2
            out[-1] = f"{step} x{count}"
        else:
            out.append(step)
    return out


@dataclass
class Outcome:
    status: str  # awaiting_review | reviewed | needs_review | auto_clear
    state: dict[str, Any]
    review_request: dict | None
    result: CheckResult | None
    trace: list[str]


class CheckWorkflow:
    """The interactive graph: start a check, watch it, pause for review, resume, inspect."""

    def __init__(self, checkpointer=None):
        self.checkpointer = checkpointer or InMemorySaver()
        self._graph = build_graph(interactive=True, checkpointer=self.checkpointer)

    @staticmethod
    def _config(ctx: RunContext | None, thread_id: str) -> dict:
        configurable: dict[str, Any] = {"thread_id": thread_id}
        if ctx is not None:
            configurable["run"] = ctx
        return {"configurable": configurable, "max_concurrency": MAX_PARALLEL}

    def stream(self, ctx: RunContext, thread_id: str) -> Iterator[dict]:
        """Run to completion or to the review pause, yielding each step as it finishes."""
        config = self._config(ctx, thread_id)
        for update in self._graph.stream({"app_id": ctx.app.app_id}, config, stream_mode="updates"):
            for node, value in update.items():
                if node == "__interrupt__":
                    yield {"node": "human_review", "paused": True, "request": value[0].value}
                else:
                    detail = {k: v for k, v in (value or {}).items() if k != "path"}
                    yield {"node": node, "detail": detail}

    def outcome(self, ctx: RunContext | None, thread_id: str) -> Outcome:
        snapshot = self._graph.get_state(self._config(ctx, thread_id))
        state = dict(snapshot.values)
        paused = bool(snapshot.next)
        request = None
        if paused:
            interrupts = [i for task in snapshot.tasks for i in task.interrupts]
            request = interrupts[0].value if interrupts else None
        return Outcome(
            status="awaiting_review" if paused else state.get("status", "auto_clear"),
            state=state,
            review_request=request,
            result=ctx.result if ctx else None,
            trace=list(state.get("path", [])),
        )

    def start(self, ctx: RunContext, thread_id: str) -> Outcome:
        for _ in self.stream(ctx, thread_id):
            pass
        return self.outcome(ctx, thread_id)

    def is_paused(self, thread_id: str) -> bool:
        return bool(self._graph.get_state(self._config(None, thread_id)).next)

    def resume(self, ctx: RunContext | None, thread_id: str, decisions: dict[str, str]) -> Outcome:
        # Validate BEFORE resuming: once a resume value is delivered, LangGraph keeps it, so a
        # value rejected inside the node would be replayed on every later attempt.
        snapshot = self._graph.get_state(self._config(ctx, thread_id))
        if not snapshot.next:
            raise LookupError(f"No paused review for thread {thread_id}")
        _validate_decisions(decisions, {f["id"] for f in snapshot.values.get("flags", [])})
        self._graph.invoke(Command(resume={"decisions": decisions}), self._config(ctx, thread_id))
        return self.outcome(ctx, thread_id)

    def history(self, thread_id: str) -> list[dict]:
        """Every checkpoint of a run, newest first: what had happened, and what ran next."""
        snapshots = list(self._graph.get_state_history(self._config(None, thread_id)))
        return [
            {
                "step": s.metadata.get("step"),
                "next": list(s.next),
                "created_at": s.created_at,
                "path": compact_path(s.values.get("path", [])),
                "flags": len(s.values.get("flags", [])),
                "notes_read": len(s.values.get("docs_read", [])),
                "status": s.values.get("status"),
            }
            for s in snapshots
        ]

    def describe(self) -> dict:
        """The graph's own structure (for the diagram), straight from LangGraph."""
        drawable = self._graph.get_graph()
        return {
            "nodes": list(drawable.nodes),
            "edges": [
                {"source": e.source, "target": e.target, "conditional": bool(e.conditional)}
                for e in drawable.edges
            ],
            "mermaid": drawable.draw_mermaid(),
        }


def main() -> None:
    import argparse
    from pathlib import Path

    from crosscheck.demo_helpers import load_demo_data
    from crosscheck.embeddings import HashingEmbedder
    from crosscheck.rules_extractor import RuleExtractor

    parser = argparse.ArgumentParser(description="Run one application through the graph.")
    parser.add_argument("app_id", nargs="?", help="for example A0001")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--rag", action="store_true", help="retrieve passages first")
    parser.add_argument("--mermaid", action="store_true", help="print the graph as Mermaid")
    parser.add_argument(
        "--decide", choices=["Confirm", "Dismiss"], help="resume with this decision"
    )
    args = parser.parse_args()

    workflow = CheckWorkflow()
    if args.mermaid:
        print(workflow.describe()["mermaid"])
        return
    if not args.app_id:
        parser.error("app_id is required unless --mermaid is given")
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
    for step in workflow.stream(ctx, app.app_id):
        print("  step:", step["node"], "(paused)" if step.get("paused") else "")
    outcome = workflow.outcome(ctx, app.app_id)
    print("path:", " -> ".join(compact_path(outcome.trace)), "| status:", outcome.status)
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
