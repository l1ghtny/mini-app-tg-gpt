import pytest
from fastapi import HTTPException

from app.api import personalization_helpers


def test_compose_prompt_from_wizard_answers_en():
    selected = {
        "answer_depth": "detailed_first",
        "follow_up": "ask_follow_up",
        "tone": "direct_professional",
    }

    prompt = personalization_helpers._compose_prompt("en", selected)

    assert prompt.startswith("Main user prompt:")
    assert "Give detailed explanations right away." in prompt
    assert "At the end of each response" in prompt
    assert "direct, pragmatic, and professional tone" in prompt


def test_validate_answers_rejects_unknown_question():
    with pytest.raises(HTTPException):
        personalization_helpers._validate_and_normalize_answers(
            [{"question_id": "unknown", "option_id": "x"}]
        )
