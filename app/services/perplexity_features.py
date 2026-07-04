from typing import Any


PERPLEXITY_SEARCH_MODE_MIN_RANK = {
    "standard": 2,
    "deep": 3,
}
PERPLEXITY_TOOL_MIN_RANK = {
    "fetch_url": 2,
    "finance_search": 3,
}
PERPLEXITY_RANK_LABELS = {
    2: "Advanced",
    3: "Premium",
}


def build_perplexity_feature_access(tier_rank: int | None) -> dict[str, list[str]]:
    rank = int(tier_rank or 0)
    search_modes = ["quick"]
    tools: list[str] = []

    for mode, min_rank in PERPLEXITY_SEARCH_MODE_MIN_RANK.items():
        if rank >= min_rank:
            search_modes.append(mode)

    for tool_name, min_rank in PERPLEXITY_TOOL_MIN_RANK.items():
        if rank >= min_rank:
            tools.append(tool_name)

    return {
        "search_modes": search_modes,
        "tools": tools,
    }


def required_rank_for_perplexity_options(
    *,
    search_mode: str | None,
    tool_names: set[str],
) -> tuple[int, list[dict[str, Any]]]:
    requirements: list[dict[str, Any]] = []

    if search_mode:
        mode_rank = PERPLEXITY_SEARCH_MODE_MIN_RANK.get(search_mode)
        if mode_rank:
            requirements.append(
                {
                    "type": "search_mode",
                    "name": search_mode,
                    "min_rank": mode_rank,
                    "min_tier": PERPLEXITY_RANK_LABELS.get(mode_rank),
                }
            )

    for tool_name in sorted(tool_names):
        tool_rank = PERPLEXITY_TOOL_MIN_RANK.get(tool_name)
        if tool_rank:
            requirements.append(
                {
                    "type": "tool",
                    "name": tool_name,
                    "min_rank": tool_rank,
                    "min_tier": PERPLEXITY_RANK_LABELS.get(tool_rank),
                }
            )

    required_rank = max((item["min_rank"] for item in requirements), default=0)
    return required_rank, requirements
