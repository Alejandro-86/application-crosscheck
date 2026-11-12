"""Text embeddings behind one small interface, so retrieval works offline and with an API.

- HashingEmbedder: free and deterministic. A signed, hashed bag of words that first expands known
  abbreviations and brand names with the terminology table (HTN also counts as "hypertension"),
  so "hypertension" retrieves a line that only says "HTN". It is lexical, not a neural model: it
  cannot match paraphrases it has no rule for.
- OpenAIEmbedder: a real embedding model, with the same disk cache, cost log and spend cap as the
  chat client. Not verified against the live API in this repo (it needs your key).
"""

import hashlib
import json
import math
import re
from array import array
from typing import Protocol

from crosscheck.cache import Cache, make_key
from crosscheck.config import ConfigError, Settings
from crosscheck.costs import UsageLog, cost_usd, load_prices
from crosscheck.terminology import canonical_terms_in

TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an the of and or to in on is are was were have had who this that with for by as at "
    "from be been it its patient mg".split()
)
SYNONYMS = {
    "nkda": ["allergy", "allergies", "drug"],
    "denies": ["no", "history"],
    "history": ["history"],
}
BATCH_SIZE = 64


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def normalise(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(v * v for v in vector))
    return [v / length for v in vector] if length else vector


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)


class HashingEmbedder:
    def __init__(self, dim: int = 2048):
        self.dim = dim
        self.name = f"hashing-v2-{dim}"

    def _tokens(self, text: str) -> list[str]:
        tokens = [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]
        extra: list[str] = []
        for token in tokens:
            extra.extend(SYNONYMS.get(token, []))
        for term in canonical_terms_in(text):
            extra.extend(TOKEN.findall(term))
        return tokens + extra

    def _embed_one(self, text: str) -> list[float]:
        counts: dict[str, int] = {}
        for token in self._tokens(text):
            counts[token] = counts.get(token, 0) + 1
        vector = [0.0] * self.dim
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            sign = 1.0 if digest[0] & 1 else -1.0  # signed, so collisions tend to cancel
            vector[int.from_bytes(digest[1:], "big") % self.dim] += sign * (1.0 + math.log(count))
        return normalise(vector)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class OpenAIEmbedder:
    def __init__(
        self,
        settings: Settings,
        *,
        client=None,
        cache: Cache | None = None,
        usage: UsageLog | None = None,
    ):
        if not settings.embed_model:
            raise ConfigError("Set OPENAI_EMBED_MODEL in .env (see .env.example).")
        if client is None:
            if not settings.openai_api_key:
                raise ConfigError("Set OPENAI_API_KEY in .env (see .env.example).")
            from openai import OpenAI

            client = OpenAI(api_key=settings.openai_api_key)
        self._client = client
        self._model = settings.embed_model
        self.name = f"openai:{settings.embed_model}"
        self._cache = cache or Cache(settings.cache_path)
        self._usage = usage or UsageLog(settings.usage_path, settings.max_spend_usd)
        self._prices = load_prices(settings.prices_path)

    def _key(self, text: str) -> str:
        return make_key("embedding", self._model, hashlib.sha256(text.encode()).hexdigest())

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: dict[int, list[float]] = {}
        missing: list[int] = []
        for index, text in enumerate(texts):
            hit = self._cache.get(self._key(text))
            if hit is None:
                missing.append(index)
            else:
                vectors[index] = json.loads(hit)
        if len(missing) < len(texts):
            self._usage.record(
                kind="embedding",
                model=self._model,
                input_tokens=0,
                output_tokens=0,
                cost=0.0,
                cached=True,
            )
        for start in range(0, len(missing), BATCH_SIZE):
            batch = missing[start : start + BATCH_SIZE]
            self._usage.check_budget()
            response = self._client.embeddings.create(
                model=self._model, input=[texts[i] for i in batch]
            )
            tokens = response.usage.prompt_tokens
            self._usage.record(
                kind="embedding",
                model=self._model,
                input_tokens=tokens,
                output_tokens=0,
                cost=cost_usd(self._prices, self._model, tokens, 0),
                cached=False,
            )
            for index, item in zip(batch, response.data, strict=True):
                vector = normalise(list(item.embedding))
                self._cache.set(self._key(texts[index]), json.dumps(vector))
                vectors[index] = vector
        return [vectors[i] for i in range(len(texts))]


def get_embedder(kind: str, settings: Settings | None = None) -> Embedder:
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "openai":
        if settings is None:
            raise ConfigError("Settings are required for the OpenAI embedder.")
        return OpenAIEmbedder(settings)
    raise ValueError(f"Unknown embedder: {kind}")
