# clinical-rag-lab

A hands-on lab for learning production LLM engineering by building one small system end to end: extraction and question answering over **synthetic clinical-style
documents**, with retrieval, guardrails, evaluation and tracing added week by week.

> Status: work in progress. Nothing here has been evaluated on real data, and no real patient or employer data is used. All documents are synthetic.

## What is already here (the scaffold)

- **Synthetic corpus**: a seeded generator for clinical-style notes with ground-truth labels and planted hard cases (negation, abbreviations and brand names, similar patients,
  mid-document dose changes). `make data` regenerates it byte-for-byte from the seed.
- **Question set**: template questions with gold answers and gold source documents, including questions no document can answer.
- **OpenAI clients** with a sqlite response cache, token and cost logging (`runs/usage.jsonl`) and a hard spend cap (`MAX_SPEND_USD`).
- **Tests and CI**: pytest and ruff run on every pull request. The tests use fake clients, so no network or key is needed.

## What is yours to build (not yet in the repo)

Retrieval and chunking, the retrieval and answer evals, LLM-as-judge, guardrails and the agent. See the plan below.

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
- Synthetic data only, with a fixed seed so results are reproducible.

## Layout

```
src/clinical_rag_lab/
  config.py       settings from env / .env
  cache.py        sqlite key-value cache
  costs.py        price table, usage log, spend cap
  llm.py          cached chat client
  embeddings.py   cached embedder
  synthetic.py    corpus generator
  questions.py    question set with gold sources
  corpus.py       load generated data
  generate.py     CLI: python -m clinical_rag_lab.generate
tests/
docs/             weekly write-ups
prices.toml       USD per 1M tokens (verify before trusting cost figures)
data/generated/   created by `make data`; gitignored
```

## Getting started

```
python -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env      # then add your key and model name
make data                 # writes data/generated/
make test
make lint
```

Costs: every call is cached and logged. Set `MAX_SPEND_USD` low while experimenting, and check `prices.toml` against the provider's pricing page.
