"""Shared test helpers."""

from crosscheck.config import Settings


def make_settings(
    tmp_path, *, chat_model="gpt-6-luna", max_spend=10.0, embed_model="embed-test"
) -> Settings:
    prices = tmp_path / "prices.toml"
    prices.write_text(
        '[models."gpt-6-luna"]\ninput = 1.0\noutput = 2.0\n'
        '[models."embed-test"]\ninput = 0.5\noutput = 0.0\n'
    )
    return Settings(
        openai_api_key="test",
        chat_model=chat_model,
        embed_model=embed_model,
        max_spend_usd=max_spend,
        cache_path=tmp_path / "cache.sqlite",
        usage_path=tmp_path / "usage.jsonl",
        prices_path=prices,
        data_dir=tmp_path / "data",
    )
