# Results: reading everything vs retrieval (rules extractor)

Synthetic data only. The same rules extractor and the same comparison rules run in every row; only the text it is given changes. 'Text sent' is the share of note characters passed to the extractor (a proxy for model tokens). The random-control rows rank passages by chance, so they show what retrieval has to beat. The topic queries and the notes' line structure are friendly to this retrieval, and every note of a patient repeats the same facts, so treat the recall as an upper bound.

| Extractor input | Text sent | Flag precision | Flag recall | Flag F1 | Missed flags |
|---|---|---|---|---|---|
| all text | 100% | 1.000 | 1.000 | 1.000 | 0 of 590 |
| top-1 per query (hashing-v2-2048) | 47% | 1.000 | 1.000 | 1.000 | 0 of 590 |
| top-2 per query (hashing-v2-2048) | 59% | 1.000 | 1.000 | 1.000 | 0 of 590 |
| top-3 per query (hashing-v2-2048) | 62% | 1.000 | 1.000 | 1.000 | 0 of 590 |
| top-5 per query (hashing-v2-2048) | 62% | 1.000 | 1.000 | 1.000 | 0 of 590 |
| top-1 per query (random-control) | 51% | 0.645 | 0.861 | 0.737 | 82 of 590 |
| top-3 per query (random-control) | 86% | 0.963 | 0.969 | 0.966 | 18 of 590 |
