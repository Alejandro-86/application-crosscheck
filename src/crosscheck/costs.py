"""Token usage, cost estimates and a hard spend cap, logged to a local JSONL file."""

import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path


class BudgetExceededError(RuntimeError):
    """Raised before an API call when cumulative spend has reached the cap."""


def load_prices(path: Path) -> dict[str, tuple[float, float]]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return {
        name: (float(prices["input"]), float(prices.get("output", 0.0)))
        for name, prices in data.get("models", {}).items()
    }


def cost_usd(
    prices: dict[str, tuple[float, float]], model: str, input_tokens: int, output_tokens: int
) -> float | None:
    """Estimated cost in USD, or None if the model has no entry in the price table."""
    if model not in prices:
        return None
    price_in, price_out = prices[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


class UsageLog:
    def __init__(self, path: Path, max_spend_usd: float):
        self.path = path
        self.max_spend_usd = max_spend_usd

    def record(
        self,
        *,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float | None,
        cached: bool,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "cached": cached,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def total_spend(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    total += json.loads(line).get("cost_usd") or 0.0
        return total

    def check_budget(self) -> None:
        spent = self.total_spend()
        if spent >= self.max_spend_usd:
            raise BudgetExceededError(
                f"Spend cap reached: ${spent:.4f} of ${self.max_spend_usd:.2f}. "
                "Raise MAX_SPEND_USD in .env if this is intended."
            )
