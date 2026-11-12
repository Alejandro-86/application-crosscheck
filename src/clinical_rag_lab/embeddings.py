"""Text embeddings with a per-text disk cache, cost logging and a spend cap."""

import json

from clinical_rag_lab.cache import Cache, make_key
from clinical_rag_lab.config import ConfigError, Settings
from clinical_rag_lab.costs import UsageLog, cost_usd, load_prices

BATCH_SIZE = 100


class Embedder:
    def __init__(
        self,
        settings: Settings,
        *,
        client=None,
        cache: Cache | None = None,
        usage: UsageLog | None = None,
    ):
        if client is None:
            if not settings.openai_api_key:
                raise ConfigError("Set OPENAI_API_KEY in .env (see .env.example).")
            from openai import OpenAI

            client = OpenAI(api_key=settings.openai_api_key)
        self._client = client
        self._model = settings.embed_model
        self._cache = cache or Cache(settings.cache_path)
        self._usage = usage or UsageLog(settings.usage_path, settings.max_spend_usd)
        self._prices = load_prices(settings.prices_path)

    def embed(self, texts: list[str]) -> list[list[float]]:
        keys = [make_key("embed", self._model, text) for text in texts]
        vectors: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for i, key in enumerate(keys):
            hit = self._cache.get(key)
            if hit is None:
                missing.append(i)
            else:
                vectors[i] = json.loads(hit)

        if len(missing) < len(texts):
            self._usage.record(
                kind="embed",
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
            data = sorted(response.data, key=lambda item: item.index)
            for i, item in zip(batch, data, strict=True):
                vectors[i] = list(item.embedding)
                self._cache.set(keys[i], json.dumps(vectors[i]))
            tokens = response.usage.prompt_tokens
            self._usage.record(
                kind="embed",
                model=self._model,
                input_tokens=tokens,
                output_tokens=0,
                cost=cost_usd(self._prices, self._model, tokens, 0),
                cached=False,
            )
        return [v for v in vectors if v is not None]
