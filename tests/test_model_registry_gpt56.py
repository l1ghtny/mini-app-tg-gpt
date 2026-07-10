from app.schemas.chat import NewMessageRequest, UpdateConversationSettingsRequest
from app.services.model_registry import (
    canonicalize_text_model,
    get_text_model_display_names,
    get_text_model_provider,
    get_text_usage_bucket,
)


def test_gpt_5_6_tiers_are_registered():
    assert get_text_model_provider("gpt-5.6-luna") == "openai"
    assert get_text_model_provider("gpt-5.6-terra") == "openai"
    assert get_text_model_provider("gpt-5.6-sol") == "openai"
    assert get_text_model_display_names("gpt-5.6-luna")[0] == "Smart"
    assert get_text_model_display_names("gpt-5.6-terra")[0] == "Balanced"
    assert get_text_model_display_names("gpt-5.6-sol")[0] == "Flagship"


def test_legacy_openai_models_share_usage_buckets_with_replacements():
    pairs = (
        ("gpt-5.4-mini", "gpt-5.6-luna"),
        ("gpt-5.4", "gpt-5.6-terra"),
        ("gpt-5.5", "gpt-5.6-sol"),
    )
    for legacy, replacement in pairs:
        assert canonicalize_text_model(legacy) == replacement
        assert get_text_usage_bucket(legacy) == get_text_usage_bucket(replacement)


def test_chat_payloads_canonicalize_legacy_models():
    message = NewMessageRequest(
        client_request_id="req-1",
        role="user",
        content=[{"type": "text", "value": "hello"}],
        model="gpt-5.5",
    )
    settings = UpdateConversationSettingsRequest(model="gpt-5.4-mini")

    assert message.model == "gpt-5.6-sol"
    assert settings.model == "gpt-5.6-luna"
