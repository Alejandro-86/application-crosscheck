# Three-minute demo

The point to land: **a model reads messy clinical text, but code decides what gets flagged, and every flag cites evidence a human can check.** All data is synthetic.

## Before you start (one minute, once)

```
cd ~/personal/application-crosscheck && . .venv/bin/activate
make data                 # only if data/generated is missing (under a minute)
ALLOW_LLM=1 make web      # open http://127.0.0.1:8000
```

- Pick **LLM** in the Extractor menu. The three demo applications below were run once, and replies are cached, so they are instant and free. A new application makes a live call (a fraction of a cent).
- No key or no network? Use **Rules (free baseline)**. It reads the templated notes but not the narrative letters, which is itself a useful point: say so and skip the retrieval step.
- Keep **Notes read** on *All text* until step 3.

## The script

| Time | Do this | Say this |
|---|---|---|
| **0:00** | Land on the page. | "Insurance applications declare conditions, medications and allergies. This checks them against clinical notes and a pharmacy database and flags what looks wrong, for a human to review. The model only extracts facts with quotes. Code decides." |
| **0:20** | Search **A0027** (Brian Nash). Open *Original application form*. Press **Run check**. | "This is the raw form as it arrived. A scraper parsed it into a database, and the app reads from there. Two discrepancies." |
| **0:35** | Click **Locate** on the asthma flag. | "He declared asthma. The note, a free-text letter, says 'There is no evidence of asthma.' and nuts is undisclosed. Every flag has a verbatim quote, and a flag whose quote can't be found in the source is dropped before you see it. The same letter mentions his father's kidney disease. It isn't treated as his." |
| **1:00** | Search **A0014** (Bella Hughes). Set **Notes read** to *Retrieved passages (RAG)*. Run check. Open the **Retrieval** tab. | "With retrieval, the model only reads the passages that match the application's items plus fixed topic questions. Highlighted lines were sent, struck-through ones weren't: 72% of the text here." |
| **1:25** | Open **Declared vs record**. | "Omeprazole: the applicant left it out, the notes say 20 mg, the pharmacy dispensed 40. That's a source conflict, a different flag from an applicant error." |
| **1:40** | Search **A0009** (Colin Clarke). Click **Review workflow**. | "Now the same check as a graph that can stop and wait for a person." |
| **1:55** | Point at the two reasons. | "It paused for two reasons: there's a flag, and the notes contain 'persistently low mood', which the system couldn't map to a known condition. A plain pipeline would have said no problem. This one says it didn't fully read the record." |
| **2:15** | Click **Confirm**, then **Submit review**. Open **History**. Open the run. Open *Reviewer feedback*. | "Decisions are written to an audit log, with the model, a hash of the prompt, what it read, the evidence and the cost. Decisions are append-only, and the feedback table shows which flag types reviewers dismiss." |
| **2:40** | Stop clicking. | "I tested it two ways. A free regression gate runs in CI. On 100 hard applications the model scored flag precision 0.96 and recall 1.0. The traps I expected to hurt it didn't. The loss was my own terminology table. It's synthetic data, one small model and one run, and it isn't deployed." |

## If something goes wrong

| Problem | Do |
|---|---|
| Model call fails or is slow | Switch to *Rules*, say the narrative letters need the model, and show the form, evidence and history instead. |
| A0027 shows no flags | Press **Run check** again with the extractor set to LLM (it only auto-runs for Rules). |
| Page is empty | `make data`, then restart `make web`. |
| Asked to show the code | `pipeline.py` (the flow), `compare.py` (`ungrounded_flags` is the guardrail), `graph.py` (routing). |

## Likely questions, short answers

- **Why not fine-tune?** Prompting, retrieval and code checks reached the numbers; fine-tuning only makes sense behind a labelled set and a measured ceiling.
- **How do you know it works?** [docs/results-llm-luna-hard.md](results-llm-luna-hard.md): extraction and flags are scored separately, per kind of hard case, with a random control for retrieval.
- **Did embeddings help?** OpenAI embeddings were no better than the free offline ones on this data, and flag results were identical, because each patient's notes repeat the same facts. A test that would separate them needs sentence-level passages and differently worded queries.
- **What stops hallucinated flags?** The model returns a quote with every fact; code checks it's in the note, maps terms through a table (unknowns are reported, not guessed), and drops any flag without verifiable evidence.
- **Biggest gap?** An undisclosed condition written in a wording the table doesn't know can be missed. The graph now routes that application to review instead of clearing it.
- **What's missing?** Deployment, monitoring and alerting, authentication, a comparison across model sizes, confidence calibration.
