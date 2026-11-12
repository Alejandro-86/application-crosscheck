# clinical-rag-lab

A hands-on lab for learning production LLM engineering by building one small system end to end: extraction and question answering over **synthetic clinical-style
documents**, with retrieval, guardrails, evaluation and tracing added week by week.

> Status: work in progress. Nothing here has been evaluated on real data, and no real patient or employer data is used. All documents are synthetic.

## Plan

| Week | Topic | What gets added to the repo |
|---|---|---|
| 1 | RAG | Baseline retrieval, then chunking, hybrid (BM25 + vector) search and reranking; recall@k measured separately from answer quality |
| 2 | Evaluation | Labelled set, LLM-as-judge validated against human labels, error analysis, a regression gate in CI |
| 3 | Guardrails | Input/output checks (PII, schema, topic), human-in-the-loop review, prompt-injection tests |
| 4 | Agents and tracing | Tool use, a LangGraph workflow, tracing and cost per request |

Each week ends with a short write-up with before/after numbers in `docs/`. Failures are written up too.

## Principles

- Measure before and after every change; report what a result does and does not prove.
- The model proposes; deterministic code decides what is allowed.
- Synthetic data only, with a generator and a fixed seed so results are reproducible.

## Layout (planned)

```
data/        synthetic document generator and labels
src/         retrieval, extraction, guardrails
evals/       eval sets, metrics, judge validation
docs/        weekly write-ups
tests/
```

## Getting started

Not yet runnable; Week 1 adds the first pipeline.
