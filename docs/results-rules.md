# Results: rules extractor

Synthetic data only. The comparison rules are deterministic; any gap below comes from extraction. The rule-based extractor works because the synthetic notes use a few templates, so strong numbers there say little about real notes.

- Applications: 600 (518 planted errors)
- Notes processed: 1000
- Applications parsed from their raw forms exactly as the truth file says: 600/600
- Extractor input: all text; 243883/243883 characters of notes sent (100%)
- Flags dropped for missing evidence: 0
- Quotes not found in the note (dropped): 0
- Terms with no canonical mapping: 0
- Notes needing a retry: 0

## Extraction (per fact kind, scored against the generator's labels)

| Kind | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| condition | 1979 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| negated_condition | 487 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| medication | 1483 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| allergy | 678 | 0 | 0 | 1.000 | 1.000 | 1.000 |

## Flags (scored against the flags a perfect extractor would produce)

| Flag type | Severity | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| undisclosed_condition | discrepancy | 72 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| contradicted_declaration | discrepancy | 51 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| undisclosed_medication | discrepancy | 89 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| dose_mismatch | discrepancy | 115 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| undisclosed_allergy | discrepancy | 80 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| source_conflict | data_quality | 68 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| unverified_declaration | unverified | 101 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| unverified_medication | unverified | 14 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **overall** |  | 590 | 0 | 0 | 1.000 | 1.000 | 1.000 |

## Examples of errors

False positives (flagged, but a perfect extractor would not):
- none

False negatives (a perfect extractor would flag, but this run did not):
- none
