import pytest
from app.services.shared_chat_provider import (
    normalize_claude_usage,
    normalize_openai_usage,
)
from app.services.allowance_policy import usage_units


def test_claude_cache_is_added_to_total_input_only_once():
    normalized = normalize_claude_usage(
        {
            "input_tokens": 100,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 300,
            "output_tokens": 400,
        }
    )
    assert normalized["input_tokens"] == 600
    assert usage_units("claude-sonnet-5", normalized) == 4990


def test_openai_reasoning_is_a_subset_of_output():
    normalized = normalize_openai_usage(
        {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 800},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 70},
        }
    )
    assert normalized["reasoning_tokens"] == 70
    assert usage_units("gpt-5.6-terra", normalized) == 1760


def test_missing_claude_usage_is_not_a_free_success():
    with pytest.raises(ValueError):
        normalize_claude_usage({"output_tokens": 20})


def test_missing_openai_usage_is_not_a_free_success():
    with pytest.raises(ValueError):
        normalize_openai_usage({"output_tokens": 20})
