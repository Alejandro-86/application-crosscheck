import json
from types import SimpleNamespace

import pytest

from clinical_rag_lab.cache import Cache, make_key
from clinical_rag_lab.config import ConfigError, Settings
from clinical_rag_lab.costs import BudgetExceededError, UsageLog, cost_usd, load_prices
from clinical_rag_lab.embeddings import Embedder
from clinical_rag_lab.llm import ChatClient


def make_settings(tmp_path, *, chat_model="gpt-6-luna", max_spend=10.0) -> Settings:
    prices = tmp_path / "prices.toml"
    prices.write_text(
        '[models."gpt-6-luna"]\ninput = 1.0\noutput = 2.0\n'
        '[models."embed-test"]\ninput = 0.5\noutput = 0.0\n'
    )
    return Settings(
        openai_api_key="test",
        chat_model=chat_model,
        embed_model="embed-test",
        max_spend_usd=max_spend,
        cache_path=tmp_path / "cache.sqlite",
        usage_path=tmp_path / "usage.jsonl",
        prices_path=prices,
        data_dir=tmp_path / "data",
    )


class FakeChat:
    def __init__(self):
        self.calls = 0
        self.completions = SimpleNamespace(create=self.create)
        self.chat = SimpleNamespace(completions=self.completions)

    def create(self, **kwargs):
        self.calls += 1
        message = SimpleNamespace(content="hello")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=500_000),
        )


class FakeEmbeddings:
    def __init__(self):
        self.calls = 0
        self.embeddings = SimpleNamespace(create=self.create)

    def create(self, *, model, input):
        self.calls += 1
        data = [
            SimpleNamespace(index=i, embedding=[float(len(t)), 1.0]) for i, t in enumerate(input)
        ]
        return SimpleNamespace(data=data, usage=SimpleNamespace(prompt_tokens=2_000_000))


def test_cache_round_trip(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    key = make_key("a", 1)
    assert cache.get(key) is None
    cache.set(key, "v")
    assert cache.get(key) == "v"
    assert make_key("a", 1) == key
    assert make_key("a", 2) != key


def test_cost_known_and_unknown_model(tmp_path):
    prices = load_prices(make_settings(tmp_path).prices_path)
    assert cost_usd(prices, "gpt-6-luna", 1_000_000, 500_000) == pytest.approx(2.0)
    assert cost_usd(prices, "mystery", 10, 10) is None


def test_chat_second_identical_call_is_cached(tmp_path):
    fake = FakeChat()
    client = ChatClient(make_settings(tmp_path), client=fake)
    messages = [{"role": "user", "content": "hi"}]
    first = client.complete(messages)
    second = client.complete(messages)
    assert fake.calls == 1
    assert first.cached is False and second.cached is True
    assert first.cost_usd == pytest.approx(2.0)
    assert second.cost_usd == 0.0
    log = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert [entry["cached"] for entry in log] == [False, True]


def test_budget_cap_blocks_new_calls(tmp_path):
    fake = FakeChat()
    client = ChatClient(make_settings(tmp_path, max_spend=1.0), client=fake)
    client.complete([{"role": "user", "content": "one"}])
    with pytest.raises(BudgetExceededError):
        client.complete([{"role": "user", "content": "two"}])
    assert fake.calls == 1


def test_chat_requires_a_model(tmp_path):
    with pytest.raises(ConfigError):
        ChatClient(make_settings(tmp_path, chat_model=""), client=FakeChat())


def test_embedder_caches_per_text(tmp_path):
    fake = FakeEmbeddings()
    embedder = Embedder(make_settings(tmp_path), client=fake)
    first = embedder.embed(["alpha", "be"])
    second = embedder.embed(["alpha", "be"])
    third = embedder.embed(["alpha", "gamma"])
    assert first == second
    assert fake.calls == 2
    assert third[0] == first[0]
    assert third[1] == [5.0, 1.0]


def test_usage_log_total(tmp_path):
    log = UsageLog(tmp_path / "u.jsonl", 5.0)
    assert log.total_spend() == 0.0
    log.record(kind="chat", model="m", input_tokens=1, output_tokens=1, cost=1.5, cached=False)
    log.record(kind="chat", model="m", input_tokens=1, output_tokens=1, cost=None, cached=False)
    assert log.total_spend() == pytest.approx(1.5)
