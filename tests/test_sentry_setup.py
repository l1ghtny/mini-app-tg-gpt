import logging

from app.core import sentry_setup


def test_build_sentry_openai_integrations_omits_tiktoken_by_default(monkeypatch):
    monkeypatch.delenv("SENTRY_OPENAI_TIKTOKEN_ENCODING", raising=False)

    captured_kwargs = {}

    class FakeOpenAIIntegration:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(sentry_setup, "OpenAIIntegration", FakeOpenAIIntegration)

    integrations = sentry_setup.build_sentry_openai_integrations(logging.getLogger("test"))

    assert len(integrations) == 1
    assert captured_kwargs == {"include_prompts": True}


def test_build_sentry_openai_integrations_uses_configured_encoding(monkeypatch):
    monkeypatch.setenv("SENTRY_OPENAI_TIKTOKEN_ENCODING", "o200k_base")

    captured_kwargs = {}

    class FakeOpenAIIntegration:
        def __init__(self, **kwargs):
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(sentry_setup, "OpenAIIntegration", FakeOpenAIIntegration)

    integrations = sentry_setup.build_sentry_openai_integrations(logging.getLogger("test"))

    assert len(integrations) == 1
    assert captured_kwargs == {
        "include_prompts": True,
        "tiktoken_encoding_name": "o200k_base",
    }


def test_build_sentry_openai_integrations_falls_back_on_init_failure(monkeypatch, caplog):
    monkeypatch.setenv("SENTRY_OPENAI_TIKTOKEN_ENCODING", "o200k_base")

    def fail_openai_integration(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(sentry_setup, "OpenAIIntegration", fail_openai_integration)
    caplog.set_level(logging.WARNING)

    integrations = sentry_setup.build_sentry_openai_integrations(logging.getLogger("test"))

    assert integrations == []
    assert "Failed to initialize Sentry OpenAI integration" in caplog.text
