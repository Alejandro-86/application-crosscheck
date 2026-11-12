"""Chat completions with a disk cache, cost logging and a spend cap."""

import json
from dataclasses import dataclass

from clinical_rag_lab.cache import Cache, make_key
from clinical_rag_lab.config import ConfigError, Settings
from clinical_rag_lab.costs import UsageLog, cost_usd, load_prices


@dataclass
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    cached: bool


class ChatClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client=None,
        cache: Cache | None = None,
        usage: UsageLog | None = None,
    ):
        if not settings.chat_model:
            raise ConfigError("Set OPENAI_CHAT_MODEL in .env (see .env.example).")
        if client is None:
            if not settings.openai_api_key:
                raise ConfigError("Set OPENAI_API_KEY in .env (see .env.example).")
            from openai import OpenAI

            client = OpenAI(api_key=settings.openai_api_key)
        self._client = client
        self._settings = settings
        self._cache = cache or Cache(settings.cache_path)
        self._usage = usage or UsageLog(settings.usage_path, settings.max_spend_usd)
        self._prices = load_prices(settings.prices_path)

    def complete(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> ChatResult:
        model = model or self._settings.chat_model
        key = make_key("chat", model, messages, temperature, max_tokens)
        hit = self._cache.get(key)
        if hit is not None:
            data = json.loads(hit)
            self._usage.record(
                kind="chat",
                model=model,
                input_tokens=0,
                output_tokens=0,
                cost=0.0,
                cached=True,
            )
            return ChatResult(data["text"], data["input_tokens"], data["output_tokens"], 0.0, True)

        self._usage.check_budget()
        kwargs: dict = {"model": model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        response = self._client.chat.completions.create(**kwargs)

        text = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens
        cost = cost_usd(self._prices, model, input_tokens, output_tokens)
        self._cache.set(
            key,
            json.dumps(
                {"text": text, "input_tokens": input_tokens, "output_tokens": output_tokens}
            ),
        )
        self._usage.record(
            kind="chat",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            cached=False,
        )
        return ChatResult(text, input_tokens, output_tokens, cost, False)
