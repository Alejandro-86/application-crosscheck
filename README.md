# crosscheck

A small demo: cross-check synthetic insurance applications against several sources and flag inaccurate or undisclosed data for a human reviewer.

> All data is synthetic. No real patient or employer data is used. Flags are data-quality signals for human review, not underwriting decisions.

## What it does

An application declares conditions, medications and allergies. The system checks those declarations against two sources:

- **Clinical notes** (free text): an extractor pulls out facts, each with a verbatim quote.
- **A pharmacy database** (structured rows): used as an independent second source for medications.

Deterministic rules compare the application with the sources and produce flags. Every discrepancy cites its evidence. If nothing is found, the flag says "unverified"; it never claims a contradiction without evidence.

```
application ──┐
notes ──► extractor (rules baseline | LLM) ──► facts + quotes ──► terminology table
pharmacy rows ─────────────────────────────────────────────────────┐        │
                                                                    ▼        ▼
                                                      deterministic comparison ──► flags
                                                                    │
                                   every discrepancy needs a verifiable quote, else it is dropped
```

| Flag | Meaning | Severity |
|---|---|---|
| `undisclosed_condition` / `undisclosed_medication` / `undisclosed_allergy` | The sources document it, the application does not | discrepancy |
| `contradicted_declaration` | Declared, but the record explicitly denies it ("No history of ...") | discrepancy |
| `dose_mismatch` | Declared dose matches none of the sources | discrepancy |
| `source_conflict` | The notes and the pharmacy disagree with each other | data quality |
| `unverified_declaration` / `unverified_medication` | Declared, but nothing in the sources mentions it (absence is not proof) | unverified |

## Design choices

- **The model only extracts; code decides.** The LLM returns facts with quotes. Code checks each quote occurs verbatim in the note (invented ones are dropped, with bounded retries), maps terms with a terminology table (unknown terms are reported, not guessed), and the comparison rules are plain Python.
- **No citation, no flag.** A discrepancy whose evidence cannot be found in the sources is removed before it reaches a reviewer.
- **Two sources.** Notes and pharmacy can disagree, which is reported separately from application errors.
- **Evaluation at two levels.** Extraction is scored against the generator's labels; flags are scored against the flags a perfect extractor would produce. The rules are identical in both, so any gap comes from extraction.
- **Cost control.** Every model call is cached on disk, logged with token counts and estimated cost, and refused once logged spend reaches `MAX_SPEND_USD`.

## Results so far

The only measured results are for the **rule-based baseline**, in [docs/results-rules.md](docs/results-rules.md): it scores 1.000 on every metric. That is expected, because the synthetic notes use a few templates, so it says little about real notes. The **LLM extractor has not been evaluated yet** (it needs an API key); run `make eval-llm` and compare. No LLM numbers are claimed.

## Limits

- Synthetic, templated notes are far easier than real clinical text.
- The terminology table is a small stand-in for a real terminology service.
- Matching patients by MRN is a simplification; no identity-mismatch checks yet.
- The review step stores decisions in a local JSON file only.

## Getting started

```
python -m venv .venv && source .venv/bin/activate
make install                  # core + dev tools
pip install -e ".[demo]"      # adds Streamlit for the demo
cp .env.example .env          # add your key and chat model name (only needed for the LLM extractor)
make data                     # writes data/generated/ from a fixed seed
make test && make lint
make eval-rules               # free baseline, writes docs/results-rules.md
make demo                     # Streamlit app on the rules baseline (LLM mode uses your key)
make eval-llm                 # LLM extractor on the first 20 applications; costs credits
```

Check `prices.toml` against the provider's pricing page before trusting any cost figure.

## Layout

```
src/crosscheck/
  synthetic.py        notes generator with planted hard cases (negation, abbreviations, brands)
  pharmacy.py         mock pharmacy source (imperfect on purpose)
  applications.py     applications with planted errors
  terminology.py      surface form -> canonical term
  rules_extractor.py  free baseline extractor
  llm_extractor.py    LLM extractor with quote grounding and retries
  compare.py          deterministic comparison and the citation guardrail
  pipeline.py         extract -> merge -> compare -> enforce grounding
  oracle.py           ground truth for scoring
  evaluate.py         extraction and flag metrics, report writer
  demo.py             Streamlit app (demo_helpers.py holds the testable logic)
  config.py cache.py costs.py llm.py   settings, response cache, spend cap, chat client
tests/                no network or key needed (fake clients)
docs/results-rules.md baseline results
```
