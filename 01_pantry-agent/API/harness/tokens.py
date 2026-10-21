"""Token counting and usage tracking: per model call, aggregated per turn, priced from PRICES."""
from __future__ import annotations

from API.harness import log

FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")

# USD per 1M tokens. `input` is uncached input; cache_write is the 5-minute-TTL write rate.
# TODO: verify these against the official pricing page before reporting costing results:
#       https://platform.claude.com/docs/en/about-claude/pricing
#       Source used: Anthropic's claude-api reference (cached 2026-10-06): Opus 5.5 $4 in / $20 out /
#       $0.20 cache read; cache write = 1.25x input for the 5-minute TTL (=> $5.00).
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "cache_read": 0.20, "cache_write": 5.00, "output": 20.00},
}
_PRICE_KEY = {"input_tokens": "input", "cache_read_input_tokens": "cache_read",
              "cache_creation_input_tokens": "cache_write", "output_tokens": "output"}


def cost(usage: dict, model: str) -> float | None:
    """USD cost of a usage dict, or None when the model has no entry in PRICES."""
    prices = PRICES.get(model)
    if prices is None:
        return None
    return sum(usage.get(f, 0) * prices[_PRICE_KEY[f]] for f in FIELDS) / 1_000_000


def record_call(usage, model: str) -> dict:
    """Turn an SDK `response.usage` into a plain dict {4 token fields, cost_usd} and log it."""
    rec = {f: getattr(usage, f, None) or 0 for f in FIELDS}
    rec["cost_usd"] = cost(rec, model)
    log(__file__, "call usage: " + " ".join(f"{f}={rec[f]}" for f in FIELDS) + f" cost={_fmt(rec['cost_usd'])}")
    return rec


def aggregate(calls: list[dict], model: str, label: str = "turn") -> dict:
    """Sum per-call usage dicts into one {4 token fields, calls, cost_usd} dict and log it."""
    total = {f: sum(c.get(f, 0) for c in calls) for f in FIELDS}
    total["calls"] = len(calls)
    total["cost_usd"] = cost(total, model)
    log(__file__, f"{label} usage: calls={total['calls']} "
                  + " ".join(f"{f}={total[f]}" for f in FIELDS) + f" cost={_fmt(total['cost_usd'])}")
    return total


def _fmt(c: float | None) -> str:
    return "n/a (model not in PRICES)" if c is None else f"${c:.4f}"
