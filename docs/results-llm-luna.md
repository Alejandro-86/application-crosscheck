# Results: llm extractor

Synthetic data only. The comparison rules are deterministic; any gap below comes from extraction. The rule-based extractor works because the synthetic notes use a few templates, so strong numbers there say little about real notes.

- Applications: 20 (21 planted errors)
- Notes processed: 31
- Applications parsed from their raw forms exactly as the truth file says: 120/120
- Extractor input: all text; 7716/7716 characters of notes sent (100%)
- Flags dropped for missing evidence: 0
- Quotes not found in the note (dropped): 0
- Terms with no canonical mapping: 1
- Notes needing a retry: 0

## Extraction (per fact kind, scored against the generator's labels)

| Kind | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| condition | 58 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| negated_condition | 20 | 0 | 1 | 1.000 | 0.952 | 0.976 |
| medication | 48 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| allergy | 22 | 0 | 0 | 1.000 | 1.000 | 1.000 |

## Flags (scored against the flags a perfect extractor would produce)

| Flag type | Severity | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| undisclosed_condition | discrepancy | 0 | 0 | 0 | n/a | n/a | n/a |
| contradicted_declaration | discrepancy | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| undisclosed_medication | discrepancy | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| dose_mismatch | discrepancy | 3 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| undisclosed_allergy | discrepancy | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| source_conflict | data_quality | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| unverified_declaration | unverified | 5 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| unverified_medication | unverified | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **overall** |  | 29 | 0 | 0 | 1.000 | 1.000 | 1.000 |

## Examples of errors

False positives (flagged, but a perfect extractor would not):
- none

False negatives (a perfect extractor would flag, but this run did not):
- none
