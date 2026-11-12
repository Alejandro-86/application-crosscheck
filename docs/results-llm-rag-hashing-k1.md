# Results: llm extractor

Synthetic data only. The comparison rules are deterministic; any gap below comes from extraction. The rule-based extractor works because the synthetic notes use a few templates, so strong numbers there say little about real notes.

- Applications: 100 (91 planted errors)
- Notes processed: 164
- Applications parsed from their raw forms exactly as the truth file says: 600/600
- Extractor input: top-1 per query (hashing-v2-2048); 32211/55189 characters of notes sent (58%)
- Flags dropped for missing evidence: 0
- Quotes not found in the note (dropped): 0
- Terms with no canonical mapping (left out of the comparison): 12
- Terms mapped by the model fallback (closed list, quote kept): 0
- Notes needing a retry: 0

## Extraction (per fact kind, scored against the generator's labels)

| Kind | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| condition | 267 | 0 | 58 | 1.000 | 0.822 | 0.902 |
| negated_condition | 64 | 0 | 19 | 1.000 | 0.771 | 0.871 |
| medication | 243 | 0 | 46 | 1.000 | 0.841 | 0.914 |
| allergy | 118 | 0 | 25 | 1.000 | 0.825 | 0.904 |

## Extraction by kind of note (a note counts under every hard case it contains)

| Hard case | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| (none) | 33 | 0 | 7 | 1.000 | 0.825 |
| abbreviation | 211 | 0 | 40 | 1.000 | 0.841 |
| brand_name | 318 | 0 | 70 | 1.000 | 0.820 |
| discontinued_medication | 133 | 0 | 37 | 1.000 | 0.782 |
| dose_change | 96 | 0 | 12 | 1.000 | 0.889 |
| dose_in_grams | 54 | 0 | 19 | 1.000 | 0.740 |
| family_history | 196 | 0 | 45 | 1.000 | 0.813 |
| narrative | 314 | 0 | 92 | 1.000 | 0.773 |
| negation | 357 | 0 | 113 | 1.000 | 0.760 |
| paraphrase | 181 | 0 | 42 | 1.000 | 0.812 |
| similar_patient | 37 | 0 | 5 | 1.000 | 0.881 |
| unlisted_paraphrase | 56 | 0 | 39 | 1.000 | 0.589 |

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
