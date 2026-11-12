import json
from types import SimpleNamespace

import pytest

from crosscheck.cache import Cache, make_key
from crosscheck.config import ConfigError
from crosscheck.costs import BudgetExceededError, UsageLog, cost_usd, load_prices
from crosscheck.llm import ChatClient
from helpers import make_settings


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


def test_usage_log_total(tmp_path):
    log = UsageLog(tmp_path / "u.jsonl", 5.0)
    assert log.total_spend() == 0.0
    log.record(kind="chat", model="m", input_tokens=1, output_tokens=1, cost=1.5, cached=False)
    log.record(kind="chat", model="m", input_tokens=1, output_tokens=1, cost=None, cached=False)
    assert log.total_spend() == pytest.approx(1.5)


def test_the_cache_is_safe_to_use_from_several_threads(tmp_path):
    import threading

    cache = Cache(tmp_path / "threads.sqlite")
    errors = []

    def work(worker):
        try:
            for i in range(50):
                cache.set(f"{worker}-{i}", str(i))
                assert cache.get(f"{worker}-{i}") == str(i)
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and cache.get("7-49") == "49"
