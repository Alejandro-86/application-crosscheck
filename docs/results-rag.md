# Results: reading everything vs retrieval (rules extractor)

Synthetic data only. The same rules extractor and the same comparison rules run in every row; only the text it is given changes. 'Text sent' is the share of note characters passed to the extractor (a proxy for model tokens). The random-control rows rank passages by chance, so they show what retrieval has to beat. The topic queries and the notes' line structure are friendly to this retrieval, and every note of a patient repeats the same facts, so treat the recall as an upper bound.

| Extractor input | Text sent | Flag precision | Flag recall | Flag F1 | Missed flags |
|---|---|---|---|---|---|
| all text | 100% | 1.000 | 1.000 | 1.000 | 0 of 131 |
| top-1 per query (hashing-v2-2048) | 48% | 1.000 | 1.000 | 1.000 | 0 of 131 |
| top-2 per query (hashing-v2-2048) | 61% | 1.000 | 1.000 | 1.000 | 0 of 131 |
| top-3 per query (hashing-v2-2048) | 63% | 1.000 | 1.000 | 1.000 | 0 of 131 |
| top-5 per query (hashing-v2-2048) | 64% | 1.000 | 1.000 | 1.000 | 0 of 131 |
| top-1 per query (random-control) | 52% | 0.647 | 0.855 | 0.737 | 19 of 131 |
| top-3 per query (random-control) | 87% | 0.977 | 0.962 | 0.969 | 5 of 131 |
