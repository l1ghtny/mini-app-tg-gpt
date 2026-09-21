"""Versioned customer capacity; amounts are internal micro-USD, never cash credit."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import json

RATE_VERSION = "2026-09-18-v1"
BASE_GRANT = 1_250_000
LUNA = "gpt-5.6-luna"
FLARE = "gpt-image-2.5-flare"


@dataclass(frozen=True)
class ModelPolicy:
    name: str
    provider: str
    group: str
    input_rate: str
    cached_rate: str
    write_rate: str
    output_rate: str
    max_output: int


MODELS = {
    p.name: p
    for p in (
        ModelPolicy(LUNA, "openai", "everyday", ".2", ".02", ".25", "1.2", 4096),
        ModelPolicy(
            "gpt-5.6-terra", "openai", "standard", "2", ".2", "2.5", "12", 8192
        ),
        ModelPolicy(
            "claude-sonnet-5", "anthropic", "standard", "2", ".2", "2.5", "10", 16384
        ),
        ModelPolicy("gpt-5.6-sol", "openai", "advanced", "4", ".4", "5", "20", 16384),
        ModelPolicy(
            "claude-opus-5", "anthropic", "advanced", "5", ".5", "6.25", "25", 16384
        ),
        ModelPolicy(
            "gpt-6-astra", "openai", "flagship", "10", "1", "12.5", "50", 16384
        ),
        ModelPolicy(
            "claude-fable-5-1",
            "anthropic",
            "flagship",
            "10",
            ".25",
            "12.5",
            "50",
            16384,
        ),
    )
}
PRIVATE_PLANS = {"Close Friends Tier": "premium", "Katush Tier": "max", "Smooth tier": "premium"}

PLANS = {
    "start": dict(name="Start", price_rub=490, multiple=1, luna_units=290_000),
    "plus": dict(name="Plus", price_rub=990, multiple=2, luna_units=500_000),
    "premium": dict(name="Premium", price_rub=2490, multiple=5, luna_units=1_000_000),
    "max": dict(name="Max", price_rub=9990, multiple=20, luna_units=3_330_000),
}


def model_access(plan: str) -> list[str]:
    return [
        name
        for name, p in MODELS.items()
        if plan in {"premium", "max"} or p.group != "flagship"
    ]


def ceil_units(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def usage_units(model: str, usage: dict) -> int:
    """Input is total incl cache; output is total incl reasoning. Never add reasoning twice."""
    p = MODELS[model]
    values = [
        int(usage.get(k, 0) or 0)
        for k in (
            "input_tokens",
            "cached_tokens",
            "cache_write_tokens",
            "output_tokens",
            "search_calls",
            "file_calls",
        )
    ]
    i, c, w, o, search, files = values
    if min(values) < 0 or c + w > i:
        raise ValueError("Invalid provider usage counters")
    return ceil_units(
        Decimal(i - c - w) * Decimal(p.input_rate)
        + Decimal(c) * Decimal(p.cached_rate)
        + Decimal(w) * Decimal(p.write_rate)
        + Decimal(o) * Decimal(p.output_rate)
        + search * 10_000
        + files * 2_500
    )


def input_upper_bound(messages: list[dict], instructions: str = "") -> int:
    # UTF-8 bytes conservatively bound text tokens; image budget is explicit.
    images = 0
    sanitized = []
    for message in messages:
        item = dict(message)
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in {
                    "input_image",
                    "image",
                }:
                    images += 1
                    parts.append({"type": "image"})
                else:
                    parts.append(part)
            item["content"] = parts
        sanitized.append(item)
    text = json.dumps(sanitized, ensure_ascii=False)
    return len((text + instructions).encode("utf-8")) + images * 16384 + 2048


def step_budget(
    model: str,
    messages: list[dict],
    instructions: str = "",
    *,
    max_output: int | None = None,
    search_calls: int = 0,
    file_calls: int = 0,
) -> int:
    p = MODELS[model]
    # Cache writes can cost more than uncached input.
    return ceil_units(
        Decimal(input_upper_bound(messages, instructions))
        * max(Decimal(p.input_rate), Decimal(p.write_rate))
        + Decimal(max_output or p.max_output) * Decimal(p.output_rate)
        + search_calls * 10_000
        + file_calls * 2_500
    )


def public_plans() -> list[dict]:
    return [
        dict(
            key=key,
            **{k: v for k, v in p.items() if k != "luna_units"},
            allowance_units=BASE_GRANT * p["multiple"],
            baseline="start",
            period="month",
            models=model_access(key),
            purchase_available=False,
        )
        for key, p in PLANS.items()
    ]


# OpenAI's 1024-square output calculator, verified 2026-09-20.
# Inputs and reference images are additional; settlement always uses returned usage.
IMAGE_OUTPUT_TOKENS = {"low": 196, "medium": 439, "high": 1756}


def image_budget(quality, prompt_bytes=1000, reference_tokens=0):
    output = IMAGE_OUTPUT_TOKENS[quality] * 30
    return (
        ceil_units(Decimal(output + reference_tokens * 8) * Decimal("1.25"))
        + prompt_bytes * 5
    )


def reference_count(messages):
    return min(
        4,
        sum(
            p.get("type") in {"input_image", "image"}
            for m in messages
            for p in m.get("content", [])
            if isinstance(p, dict)
        ),
    )


def output_target(model, effort="medium", required_tool=None):
    target = 2048 if required_tool else 8192 if effort == "high" else 4096
    return min(target, MODELS[model].max_output)


def affordable_output(model, messages, instructions, budget, *, target):
    base = step_budget(model, messages, instructions, max_output=1)
    base -= ceil_units(Decimal(MODELS[model].output_rate))
    remaining = Decimal(max(0, budget - base)) / Decimal(MODELS[model].output_rate)
    return min(target, int(remaining))
