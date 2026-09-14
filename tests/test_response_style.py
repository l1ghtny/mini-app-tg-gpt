import pytest

from app.services import google_service, openai_service
from app.services.response_style import STYLE_GUIDE


@pytest.mark.parametrize("instructions", [None, "", "Keep the supplied citations."])
def test_openai_style_is_separated_from_instructions_and_preserves_commentary(instructions):
    result = openai_service._instructions_for_openai("gpt-5.6-sol", instructions)
    expected_prefix = f"{instructions}\n\n" if instructions else ""
    assert result == expected_prefix + STYLE_GUIDE + openai_service.COMMENTARY_GUIDE


def test_legacy_openai_keeps_style_without_unsupported_commentary():
    assert openai_service._instructions_for_openai("gpt-5.2", None) == STYLE_GUIDE


def test_chat_providers_share_presentation_defaults():
    assert google_service.STYLE_GUIDE is STYLE_GUIDE
    assert openai_service.STYLE_GUIDE is STYLE_GUIDE
