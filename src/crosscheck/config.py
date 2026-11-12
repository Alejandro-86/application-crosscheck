"""Settings loaded from the environment (and a local .env file, which is gitignored)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    chat_model: str
    embed_model: str
    max_spend_usd: float
    cache_path: Path
    usage_path: Path
    prices_path: Path
    data_dir: Path


def get_settings(*, load_env: bool = True) -> Settings:
    if load_env:
        load_dotenv(REPO_ROOT / ".env")
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        chat_model=os.environ.get("OPENAI_CHAT_MODEL", ""),
        embed_model=os.environ.get("OPENAI_EMBED_MODEL", ""),
        max_spend_usd=float(os.environ.get("MAX_SPEND_USD", "10")),
        cache_path=REPO_ROOT / ".cache" / "llm_cache.sqlite",
        usage_path=REPO_ROOT / "runs" / "usage.jsonl",
        prices_path=REPO_ROOT / "prices.toml",
        data_dir=REPO_ROOT / "data" / "generated",
    )
