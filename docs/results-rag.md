# Results: reading everything vs retrieval (rules extractor)

Synthetic data only. The same rules extractor and the same comparison rules run in every row; only the text it is given changes. 'Text sent' is the share of note characters passed to the extractor (a proxy for model tokens). The random-control rows rank passages by chance, so they show what retrieval has to beat. The topic queries and the notes' line structure are friendly to this retrieval, and every note of a patient repeats the same facts, so treat the recall as an upper bound.

| Extractor input | Text sent | Flag precision | Flag recall | Flag F1 | Missed flags |
|---|---|---|---|---|---|
| all text | 100% | 0.558 | 0.842 | 0.671 | 97 of 614 |
| top-1 per query (hashing-v2-2048) | 55% | 0.558 | 0.842 | 0.671 | 97 of 614 |
| top-2 per query (hashing-v2-2048) | 68% | 0.558 | 0.842 | 0.671 | 97 of 614 |
| top-3 per query (hashing-v2-2048) | 69% | 0.558 | 0.842 | 0.671 | 97 of 614 |
| top-5 per query (hashing-v2-2048) | 70% | 0.558 | 0.842 | 0.671 | 97 of 614 |
| top-1 per query (random-control) | 57% | 0.414 | 0.748 | 0.533 | 155 of 614 |
| top-3 per query (random-control) | 89% | 0.531 | 0.822 | 0.645 | 109 of 614 |
