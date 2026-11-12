import json
import math
from types import SimpleNamespace

import pytest

from crosscheck.config import ConfigError
from crosscheck.corpus import LoadedDocument
from crosscheck.costs import BudgetExceededError
from crosscheck.embeddings import (
    HashingEmbedder,
    OpenAIEmbedder,
    cosine,
    get_embedder,
    pack,
    unpack,
)
from helpers import make_settings


class FakeEmbeddings:
    """Returns a 3-dim vector per input and reports 1M prompt tokens per call."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.embeddings = SimpleNamespace(create=self.create)

    def create(self, *, model, input):
        self.calls.append(list(input))
        data = [SimpleNamespace(embedding=[float(len(text)), 1.0, 0.0]) for text in input]
        return SimpleNamespace(data=data, usage=SimpleNamespace(prompt_tokens=1_000_000))


def doc(doc_id, text):
    return LoadedDocument(doc_id, text, "P1", "note", [], [])


def test_hashing_embedder_is_deterministic_and_unit_length():
    embedder = HashingEmbedder()
    first = embedder.embed(["Diagnoses: HTN; asthma."])[0]
    again = embedder.embed(["Diagnoses: HTN; asthma."])[0]
    assert first == again
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)
    assert embedder.embed([""])[0] == [0.0] * embedder.dim


def test_abbreviations_and_brand_names_match_their_canonical_term():
    embedder = HashingEmbedder()
    query, htn, unrelated = embedder.embed(
        ["hypertension", "Diagnoses: HTN.", "Diagnoses: migraine."]
    )
    assert cosine(query, htn) > cosine(query, unrelated)
    drug, brand, other = embedder.embed(
        ["metformin 500 mg", "Glucophage 500 mg twice daily", "sertraline 50 mg"]
    )
    assert cosine(drug, brand) > cosine(drug, other)


def test_vectors_survive_the_blob_round_trip():
    vector = HashingEmbedder(dim=16).embed(["asthma"])[0]
    assert unpack(pack(vector)) == pytest.approx(vector, abs=1e-6)


def test_openai_embedder_batches_caches_and_logs_cost(tmp_path):
    fake = FakeEmbeddings()
    embedder = OpenAIEmbedder(make_settings(tmp_path), client=fake)
    texts = [f"text {i}" for i in range(70)]
    first = embedder.embed(texts)
    assert [len(call) for call in fake.calls] == [64, 6]
    assert all(math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0) for vec in first)
    again = embedder.embed(texts[:5] + ["brand new"])
    assert len(fake.calls) == 3 and fake.calls[2] == ["brand new"]
    assert again[:5] == first[:5]
    log = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert [e["kind"] for e in log] == ["embedding"] * len(log)
    assert sum(e["cost_usd"] for e in log) == pytest.approx(3 * 0.5)


def test_openai_embedder_respects_the_spend_cap(tmp_path):
    fake = FakeEmbeddings()
    embedder = OpenAIEmbedder(make_settings(tmp_path, max_spend=0.4), client=fake)
    embedder.embed(["one"])
    with pytest.raises(BudgetExceededError):
        embedder.embed(["two"])
    assert len(fake.calls) == 1


def test_openai_embedder_needs_a_model_and_unknown_kinds_fail(tmp_path):
    with pytest.raises(ConfigError):
        OpenAIEmbedder(make_settings(tmp_path, embed_model=""), client=FakeEmbeddings())
    assert get_embedder("hashing").name.startswith("hashing")
    with pytest.raises(ValueError):
        get_embedder("magic")
