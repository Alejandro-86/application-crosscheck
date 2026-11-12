# Results: llm extractor

Synthetic data only. The comparison rules are deterministic; any gap below comes from extraction. The rule-based extractor works because the synthetic notes use a few templates, so strong numbers there say little about real notes.

- Applications: 100 (91 planted errors)
- Notes processed: 164
- Applications parsed from their raw forms exactly as the truth file says: 600/600
- Extractor input: all text; 55189/55189 characters of notes sent (100%)
- Flags dropped for missing evidence: 0
- Quotes not found in the note (dropped): 0
- Terms with no canonical mapping: 17
- Notes needing a retry: 0

## Extraction (per fact kind, scored against the generator's labels)

| Kind | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| condition | 308 | 0 | 17 | 1.000 | 0.948 | 0.973 |
| negated_condition | 83 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| medication | 289 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| allergy | 143 | 0 | 0 | 1.000 | 1.000 | 1.000 |

## Extraction by kind of note (a note counts under every hard case it contains)

| Hard case | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| (none) | 40 | 0 | 0 | 1.000 | 1.000 |
| abbreviation | 251 | 0 | 0 | 1.000 | 1.000 |
| brand_name | 379 | 0 | 9 | 1.000 | 0.977 |
| discontinued_medication | 162 | 0 | 8 | 1.000 | 0.953 |
| dose_change | 105 | 0 | 3 | 1.000 | 0.972 |
| dose_in_grams | 71 | 0 | 2 | 1.000 | 0.973 |
| family_history | 233 | 0 | 8 | 1.000 | 0.967 |
| narrative | 389 | 0 | 17 | 1.000 | 0.958 |
| negation | 455 | 0 | 15 | 1.000 | 0.968 |
| paraphrase | 221 | 0 | 2 | 1.000 | 0.991 |
| similar_patient | 42 | 0 | 0 | 1.000 | 1.000 |
| unlisted_paraphrase | 78 | 0 | 17 | 1.000 | 0.821 |

## Flags (scored against the flags a perfect extractor would produce)

| Flag type | Severity | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| undisclosed_condition | discrepancy | 7 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| contradicted_declaration | discrepancy | 8 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| undisclosed_medication | discrepancy | 23 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| dose_mismatch | discrepancy | 19 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| undisclosed_allergy | discrepancy | 16 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| source_conflict | data_quality | 15 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| unverified_declaration | unverified | 18 | 5 | 0 | 0.783 | 1.000 | 0.878 |
| unverified_medication | unverified | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **overall** |  | 108 | 5 | 0 | 0.956 | 1.000 | 0.977 |

## Examples of errors

False positives (flagged, but a perfect extractor would not):
- A0008: unverified_declaration / depression
- A0039: unverified_declaration / depression
- A0065: unverified_declaration / migraine
- A0076: unverified_declaration / atrial fibrillation
- A0094: unverified_declaration / atrial fibrillation

False negatives (a perfect extractor would flag, but this run did not):
- none
