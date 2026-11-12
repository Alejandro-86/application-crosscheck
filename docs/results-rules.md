# Results: rules extractor

Synthetic data only. The comparison rules are deterministic; any gap below comes from extraction. The rule-based extractor works because the synthetic notes use a few templates, so strong numbers there say little about real notes.

- Applications: 600 (540 planted errors)
- Notes processed: 1000
- Applications parsed from their raw forms exactly as the truth file says: 600/600
- Extractor input: all text; 327296/327296 characters of notes sent (100%)
- Flags dropped for missing evidence: 0
- Quotes not found in the note (dropped): 0
- Terms with no canonical mapping: 0
- Notes needing a retry: 0

## Extraction (per fact kind, scored against the generator's labels)

| Kind | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| condition | 1126 | 0 | 853 | 1.000 | 0.569 | 0.725 |
| negated_condition | 270 | 0 | 308 | 1.000 | 0.467 | 0.637 |
| medication | 851 | 0 | 733 | 1.000 | 0.537 | 0.699 |
| allergy | 389 | 0 | 349 | 1.000 | 0.527 | 0.690 |

## Extraction by kind of note (a note counts under every hard case it contains)

| Hard case | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| (none) | 352 | 0 | 0 | 1.000 | 1.000 |
| abbreviation | 1506 | 0 | 0 | 1.000 | 1.000 |
| brand_name | 935 | 0 | 965 | 1.000 | 0.492 |
| discontinued_medication | 0 | 0 | 1168 | n/a | 0.000 |
| dose_change | 171 | 0 | 467 | 1.000 | 0.268 |
| dose_in_grams | 0 | 0 | 327 | n/a | 0.000 |
| family_history | 0 | 0 | 1432 | n/a | 0.000 |
| narrative | 0 | 0 | 2243 | n/a | 0.000 |
| negation | 1384 | 0 | 1695 | 1.000 | 0.449 |
| paraphrase | 0 | 0 | 1132 | n/a | 0.000 |
| similar_patient | 403 | 0 | 0 | 1.000 | 1.000 |
| unlisted_paraphrase | 0 | 0 | 555 | n/a | 0.000 |

## Flags (scored against the flags a perfect extractor would produce)

| Flag type | Severity | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| undisclosed_condition | discrepancy | 56 | 0 | 17 | 1.000 | 0.767 | 0.868 |
| contradicted_declaration | discrepancy | 26 | 0 | 28 | 1.000 | 0.481 | 0.650 |
| undisclosed_medication | discrepancy | 97 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| dose_mismatch | discrepancy | 125 | 21 | 2 | 0.856 | 0.984 | 0.916 |
| undisclosed_allergy | discrepancy | 55 | 0 | 26 | 1.000 | 0.679 | 0.809 |
| source_conflict | data_quality | 46 | 0 | 24 | 1.000 | 0.657 | 0.793 |
| unverified_declaration | unverified | 102 | 361 | 0 | 0.220 | 1.000 | 0.361 |
| unverified_medication | unverified | 10 | 27 | 0 | 0.270 | 1.000 | 0.426 |
| **overall** |  | 517 | 409 | 97 | 0.558 | 0.842 | 0.671 |

## Examples of errors

False positives (flagged, but a perfect extractor would not):
- A0007: dose_mismatch / metformin
- A0007: unverified_declaration / migraine
- A0007: unverified_declaration / osteoarthritis
- A0007: unverified_declaration / type 2 diabetes mellitus
- A0008: dose_mismatch / sertraline
- A0008: unverified_declaration / depression
- A0008: unverified_declaration / heart failure
- A0008: unverified_medication / bisoprolol
- A0012: unverified_declaration / type 2 diabetes mellitus
- A0013: dose_mismatch / bisoprolol

False negatives (a perfect extractor would flag, but this run did not):
- A0007: source_conflict / metformin
- A0008: source_conflict / sertraline
- A0012: undisclosed_allergy / penicillin
- A0013: source_conflict / bisoprolol
- A0020: contradicted_declaration / migraine
- A0021: dose_mismatch / paracetamol
- A0023: undisclosed_allergy / aspirin
- A0027: contradicted_declaration / asthma
- A0027: undisclosed_allergy / nuts
- A0031: undisclosed_allergy / sulfonamides
