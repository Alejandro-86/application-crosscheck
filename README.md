# crosscheck

A small demo: cross-check synthetic insurance applications against several sources and flag inaccurate or undisclosed data for a human reviewer.

> All data is synthetic. No real patient or employer data is used. Flags are data-quality signals for human review, not underwriting decisions.

## What it does

An application arrives as a **full raw-text form**. A scraper parses it into a database, and the app reads from that database. The declared conditions, medications and allergies are then checked against two sources:

- **Clinical notes** (free text), also loaded into the database, split into passages and embedded. Retrieval finds the passages that matter, and an extractor pulls facts out of them, each with a verbatim quote.
- **A pharmacy database** (a SQLite file, `data/generated/pharmacy.db`, one row per dispensed medication per applicant): an independent second source for medications. The app opens it read-only and only runs parameterised queries.

Deterministic rules compare the application with the sources and produce flags. Every discrepancy cites its evidence. If nothing is found, the flag says "unverified"; it never claims a contradiction without evidence.

```
 raw files                    ingestion (the scraper)                 store.db (read-only for the app)
 applications/A0001.txt ──┐   parse form, map terms, log issues  ──►  applications + declared items
 docs/D0001.txt ──────────┴─► chunk notes, embed passages       ──►  notes + passages + vectors + issues

 application ──────────────────────────────────────────────┐
 notes ─► retrieval (top-k passages per query) ─► extractor (rules | LLM) ─► facts + quotes
 pharmacy.db rows ─────────────────────────────────────────┤                      │
                                                           ▼                      ▼
                                              deterministic comparison ◄── terminology table
                                                           │
                          every discrepancy needs a verifiable quote, else it is dropped
```

The notes can also be read in full (no retrieval). Retrieval is optional so you can measure what it saves and what it costs.

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
- **Cost control.** Every model and embedding call is cached on disk, logged with token counts and estimated cost, and refused once logged spend reaches `MAX_SPEND_USD`.
- **The scraper does not guess.** A form missing a required field is not loaded and the reason is logged; a term with no canonical mapping is stored as written with a warning. Ingestion is idempotent (unchanged files are skipped by content hash, edits are replaced, deletions are removed) and one transaction, so a failure leaves the database as it was.
- **A model fallback for unknown terms, on a closed list.** When the terminology table cannot map a term the model found ("sugar diabetes"), an optional `ConceptMapper` shows the model that one term and the list of canonical names and accepts only an answer that is exactly an entry of the list, otherwise "none". It is asked once per distinct term, only for conditions and medications, and the fact keeps its original grounded quote and is marked as model-mapped. It is off unless you pass `--mapper llm` to the evaluation, so you can measure the difference.
- **"Not found" says what it could not read.** An unverified flag lists the note terms the system could not map (conditions for a condition, medications for a medication), so a gap in the terminology table shows up as a prompt to read the note rather than as a confident "absent".
- **Retrieval asks fixed topic questions plus one per declared item.** Declared items alone could never find something the applicant left out, so diagnoses, medications, allergies and denials are always asked about. The extractor only sees the retrieved lines, and the quote guardrail still applies to them.
- **Two embedders behind one interface.** A free, deterministic offline embedder (a hashed bag of words that first expands abbreviations and brand names with the terminology table) and an OpenAI embedder with the cache, cost log and spend cap. The offline one is lexical, not neural: it matches words and known abbreviations, not paraphrases.

## Results so far

Measured so far, on synthetic data. The default dataset is 1,000 notes and 600 applications; 40% of the notes are **hard**: narrative letters with paraphrased conditions (some in the terminology table, some deliberately not), doses in grams, differently phrased negations, and two traps that must not become facts (a relative's condition, a drug the patient used to take). `make data` writes it; `--hard-fraction 0` gives the easy templated version.

- [docs/results-rules.md](docs/results-rules.md): the free rules extractor scores 1.000 on the templated notes, and reads **none** of the narrative letters (recall 0.00 under `narrative`, `paraphrase`, `family_history`, `dose_in_grams`). Overall flag F1 falls to 0.67, and precision on `unverified` flags to 0.22, because anything the extractor misses surfaces as "not found". That is the baseline a language model has to beat. All 600 raw forms still parse exactly.
- [docs/results-rag.md](docs/results-rag.md): reading everything vs retrieval vs a random control. On templated notes, retrieving the top passage per query sends about half the text and loses no flags, while random selection of a similar amount loses many. On the hard notes the rules extractor cannot read the letters either way, so this table cannot show retrieval loss there; use the LLM extractor for that.

### LLM extractor results (small, low-cost model; synthetic data; one run each)

| Run | Data | Applications | Condition recall | Flag precision / recall | Report |
|---|---|---|---|---|---|
| Rules baseline | hard notes | 600 | 0.569 | 0.558 / 0.842 | [results-rules.md](docs/results-rules.md) |
| LLM | templated notes only | 20 | 1.000 | 1.000 / 1.000 | [results-llm-luna.md](docs/results-llm-luna.md) |
| LLM | hard notes (40%) | 100 | 0.948 | 0.956 / 1.000 | [results-llm-luna-hard.md](docs/results-llm-luna-hard.md) |
| LLM + closed-list mapper | hard notes (40%) | 100 | 0.954 | 0.956 / 1.000 | [results-llm-luna-hard-mapped.md](docs/results-llm-luna-hard-mapped.md) |

Findings:
- **The traps did not leak.** There were no false positives from the family-history or stopped-drug sentences, and no loss on doses written in grams or on dose changes, with the extraction prompt unchanged. (I expected some; that was a wrong prediction, not a tuned result.)
- **The loss was the terminology table, not the model.** All 17 missed conditions were wordings the table does not know ("persistently low mood", "irregular pulse"). The model found and quoted them; the table could not map them, so they were reported as unmapped and left out of the comparison.
- **The closed-list mapper helped little, on purpose.** It mapped the one unambiguous wording ("raised pressure readings" to hypertension, correct) and answered "none" for four vague ones that are symptoms rather than diagnoses ("irregular pulse" is not atrial fibrillation). The generator's labels treat those as the diagnosis, so they are generous. The prompt was deliberately not loosened to raise the score on my own labels.
- **A miss now shows up where a reviewer will see it.** The 5 false `unverified_declaration` flags (declared depression, note says "persistently low mood") now say which note terms the system could not map, so the reviewer reads the note instead of trusting "not found".
- **Caveats:** one small model, one run each, 100 applications, synthetic and partly self-authored wordings. The LLM has not been compared across model tiers, and the OpenAI embedder has not been run. To reproduce: `python -m crosscheck.evaluate --extractor llm --limit 100 [--mapper llm] --out docs/results-llm.md` (a few cents).

## Web app and API

`make web` serves a single-page app (plain HTML/CSS/JS, no build step) and a JSON API from one process.

- **Case list:** a searchable sidebar of the applications in the store, with a badge showing planted errors, then the number of flags once checked. Start from a blank application too. The top bar shows what the scraper loaded.
- **Raw form:** the Application tab shows the original form text the scraper parsed, with any terms it could not map. Declared items keep the applicant's wording (HTN, brand names).
- **Retrieval:** set **Notes read** to *Retrieved passages (RAG)* and choose top-k. A **Retrieval** tab shows which note lines were sent (highlighted) and which were skipped (struck through), with similarity scores and the share of text sent. The Clinical notes tab has a search box over the applicant's embedded passages.
- **Pharmacy database:** the Pharmacy tab shows the applicant's rows read from the database, and you can look up any applicant ID (MRN) to load their rows.
- **Editors:** declared conditions and allergies are chip inputs (abbreviations such as HTN work); medications and pharmacy rows are editable rows; clinical notes are text areas you can add, remove or paste into.
- **Live checking:** with the rules extractor the check re-runs shortly after every edit (or press Ctrl/Cmd+Enter). The model extractor only runs when you press **Run check**.
- **Results:** summary cards (discrepancies, source conflicts, unverified, reviewed) that double as filters; flag cards with evidence quotes and Confirm / Dismiss decisions; a **Declared vs record** table comparing the application, the notes and the pharmacy side by side; and an **Evidence** view where the cited passages are highlighted and **Locate** jumps to them.
- **Export:** copy a Markdown report or download the JSON result.
- **Dark mode:** the toggle in the top right switches themes. The choice is remembered, and the first visit follows your system setting.

| Endpoint | Purpose |
|---|---|
| `POST /api/check` | Body: application, notes, pharmacy rows, extractor (`rules` or `llm`), `retrieval` (`all` or `rag`) and `top_k`. Returns flags with evidence, unmapped terms, what was extracted and, for RAG, which passages were sent. |
| `GET /api/applications`, `GET /api/applications/{id}`, `GET /api/applications/{id}/answer-key` | Browse the applications in the store (raw form text, notes and pharmacy rows come from the databases) and their planted errors. |
| `GET /api/applications/{id}/retrieve?q=&k=` | Search the applicant's embedded note passages; without `q`, shows what a check would read. |
| `GET /api/ingest/issues` | What the scraper loaded, and every error or warning it logged. |
| `GET /api/pharmacy/{mrn}` | Look up one applicant's dispensing rows in the pharmacy database. |
| `GET/PUT /api/reviews` | Reviewer decisions, stored in a local JSON file. |

Safety defaults: the server binds to localhost, input sizes and shapes are validated, all text is rendered as text (never as HTML), and the model extractor is **off** unless the server is
started with `ALLOW_LLM=1` (it then uses your key and counts against `MAX_SPEND_USD`). There is no authentication, so do not expose it publicly with the LLM enabled.

## Limits

- Synthetic, templated notes are far easier than real clinical text.
- The terminology table is a small stand-in for a real terminology service.
- The offline embedder cannot match paraphrases ("blood pressure" will not find "HTN"); a real embedding model can. The OpenAI path is implemented and tested with a fake client, but not run against the live API here.
- Notes are chunked one line per passage. That suits short templated notes; long real records need larger, overlapping chunks.
- Ingestion is a command you run, not a service: there is no watcher, queue or retry.
- Matching patients by MRN is a simplification; no identity-mismatch checks yet.
- The review step stores decisions in a local JSON file only.
- The pharmacy database is a single local SQLite file with one table; a real system would query a pharmacy service with authentication and audit logging.

## Getting started

```
python -m venv .venv && source .venv/bin/activate
make install                  # core + dev tools
pip install -e ".[demo]"      # adds Streamlit for the demo
cp .env.example .env          # add your key and chat model name (only needed for the LLM extractor)
make data                     # 1000 notes (40% hard), raw forms, pharmacy.db; then runs the scraper (store.db)
make test && make lint
make eval-rules               # free baseline, writes docs/results-rules.md
make eval-rag                 # all text vs retrieval vs a random control, writes docs/results-rag.md
make ingest                   # re-run the scraper (idempotent)
python -m crosscheck.ingest --embedder openai   # real embeddings: set OPENAI_EMBED_MODEL, costs credits
make demo                     # Streamlit app on the rules baseline (LLM mode uses your key)
pip install -e ".[web]"       # adds FastAPI and uvicorn for the web app
make web                      # web app + API on http://127.0.0.1:8000 (API docs at /api/docs)
make eval-llm                 # LLM extractor on the first 20 applications; costs credits
```

Check `prices.toml` against the provider's pricing page before trusting any cost figure.

## Layout

```
src/crosscheck/
  synthetic.py        notes generator with planted hard cases (negation, abbreviations, brands)
  pharmacy.py         synthetic pharmacy rows (imperfect on purpose)
  pharmacy_db.py      the SQLite pharmacy database: create, migrate, read-only queries
  applications.py     applications with planted errors (the truth and the answer key)
  hard_notes.py       narrative letters with paraphrases, grams, family-history and stopped-drug traps
  forms.py            renders applications as messy raw-text forms (two layouts, abbreviations, brands)
  ingest.py           the scraper: parse forms and notes, chunk, embed, load store.db idempotently
  store.py            the SQLite application store: schema and read-only queries
  embeddings.py       offline hashing embedder and the cached, cost-capped OpenAI embedder
  retrieval.py        note chunks, topic + declared-item queries, top-k ranking, reduced documents
  terminology.py      surface form -> canonical term
  rules_extractor.py  free baseline extractor
  llm_extractor.py    LLM extractor with quote grounding and retries
  compare.py          deterministic comparison and the citation guardrail
  pipeline.py         (retrieve ->) extract -> merge -> compare -> enforce grounding
  oracle.py           ground truth for scoring
  evaluate.py         extraction and flag metrics, report writer
  demo.py             Streamlit app (demo_helpers.py holds the testable logic)
  api.py, web/        FastAPI app and the static page
  config.py cache.py costs.py llm.py   settings, response cache, spend cap, chat client
tests/                no network or key needed (fake clients)
docs/results-rules.md, docs/results-rag.md   measured results
```
