"""Retrieval over clinical-note chunks: send the extractor only the passages that matter.

Notes are split into one chunk per line. For an application we ask a fixed set of topic questions
(so something the applicant left out can still be found) plus one question per declared item,
take the top-k chunks for each, and keep the union. The extractor then reads only those chunks.
The guardrail is unchanged: every flag still needs a verbatim quote from the text it was given.
"""

from dataclasses import dataclass, field

from crosscheck.applications import Application
from crosscheck.corpus import LoadedDocument
from crosscheck.embeddings import Embedder, cosine

TOPIC_QUERIES = [
    "diagnoses assessment diagnosed has medical conditions",
    "medications current medication doses mg daily changed increased",
    "allergies allergy NKDA no known drug allergies",
    "no history of denies any history of",
]
DEFAULT_K = 3


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    idx: int
    text: str


@dataclass
class Hit:
    chunk: Chunk
    score: float
    queries: list[str] = field(default_factory=list)


@dataclass
class RetrievalInfo:
    embedder: str
    k: int
    queries: list[str]
    hits: list[Hit]
    chars_total: int
    chars_sent: int


Index = list[tuple[Chunk, list[float]]]


def chunk_note(doc_id: str, text: str) -> list[Chunk]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [Chunk(doc_id, idx, line) for idx, line in enumerate(lines)]


def build_index(docs: list[LoadedDocument], embedder: Embedder) -> Index:
    chunks = [chunk for doc in docs for chunk in chunk_note(doc.doc_id, doc.text)]
    return list(zip(chunks, embedder.embed([c.text for c in chunks]), strict=True))


def queries_for(app: Application) -> list[str]:
    queries = list(TOPIC_QUERIES)
    queries += [c for c in app.declared_conditions]
    queries += [f"{m.name} {m.dose} {m.unit}" for m in app.declared_medications]
    queries += [a for a in app.declared_allergies]
    return queries


def rank(index: Index, embedder: Embedder, queries: list[str], k: int = DEFAULT_K) -> list[Hit]:
    """Top-k chunks per query (scores above zero only), merged and in document order."""
    if not index or not queries:
        return []
    found: dict[tuple[str, int], Hit] = {}
    for query, vector in zip(queries, embedder.embed(queries), strict=True):
        scored = sorted(
            ((cosine(vector, chunk_vector), chunk) for chunk, chunk_vector in index),
            key=lambda pair: (-pair[0], pair[1].doc_id, pair[1].idx),
        )
        for score, chunk in scored[:k]:
            if score <= 0:
                break
            hit = found.setdefault((chunk.doc_id, chunk.idx), Hit(chunk, score))
            hit.score = max(hit.score, score)
            hit.queries.append(query)
    return sorted(found.values(), key=lambda h: (h.chunk.doc_id, h.chunk.idx))


def reduce_documents(docs: list[LoadedDocument], hits: list[Hit]) -> list[LoadedDocument]:
    """Copies of the documents holding only the retrieved lines; documents with none are dropped."""
    by_doc: dict[str, list[str]] = {}
    for hit in hits:
        by_doc.setdefault(hit.chunk.doc_id, []).append(hit.chunk.text)
    return [
        LoadedDocument(
            doc.doc_id,
            "\n".join(by_doc[doc.doc_id]) + "\n",
            doc.patient_id,
            doc.doc_type,
            doc.fields_present,
            doc.hard_cases,
        )
        for doc in docs
        if doc.doc_id in by_doc
    ]
