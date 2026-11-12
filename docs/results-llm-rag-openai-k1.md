# Results: llm extractor

Synthetic data only. The comparison rules are deterministic; any gap below comes from extraction. The rule-based extractor works because the synthetic notes use a few templates, so strong numbers there say little about real notes.

- Applications: 100 (91 planted errors)
- Notes processed: 164
- Applications parsed from their raw forms exactly as the truth file says: 600/600
- Extractor input: top-1 per query (openai:text-embedding-3-small); 33763/55189 characters of notes sent (61%)
- Flags dropped for missing evidence: 0
- Quotes not found in the note (dropped): 0
- Terms with no canonical mapping (left out of the comparison): 12
- Terms mapped by the model fallback (closed list, quote kept): 0
- Notes needing a retry: 1

## Extraction (per fact kind, scored against the generator's labels)

| Kind | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| condition | 263 | 0 | 62 | 1.000 | 0.809 | 0.895 |
| negated_condition | 66 | 0 | 17 | 1.000 | 0.795 | 0.886 |
| medication | 249 | 0 | 40 | 1.000 | 0.862 | 0.926 |
| allergy | 116 | 0 | 27 | 1.000 | 0.811 | 0.896 |

## Extraction by kind of note (a note counts under every hard case it contains)

| Hard case | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| (none) | 33 | 0 | 7 | 1.000 | 0.825 |
| abbreviation | 221 | 0 | 30 | 1.000 | 0.880 |
| brand_name | 315 | 0 | 73 | 1.000 | 0.812 |
| discontinued_medication | 127 | 0 | 43 | 1.000 | 0.747 |
| dose_change | 92 | 0 | 16 | 1.000 | 0.852 |
| dose_in_grams | 62 | 0 | 11 | 1.000 | 0.849 |
| family_history | 195 | 0 | 46 | 1.000 | 0.809 |
| narrative | 313 | 0 | 93 | 1.000 | 0.771 |
| negation | 378 | 0 | 92 | 1.000 | 0.804 |
| paraphrase | 184 | 0 | 39 | 1.000 | 0.825 |
| similar_patient | 38 | 0 | 4 | 1.000 | 0.905 |
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
