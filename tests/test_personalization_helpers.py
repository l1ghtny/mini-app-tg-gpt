import pytest
from fastapi import HTTPException

from app.api import personalization_helpers


def _iter_ru_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "ru" and isinstance(item, str):
                yield item
            yield from _iter_ru_strings(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_ru_strings(item)


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


def test_compose_prompt_from_wizard_answers_ru():
    selected = {
        "answer_depth": "detailed_first",
        "follow_up": "ask_follow_up",
        "tone": "direct_professional",
    }

    prompt = personalization_helpers._compose_prompt("ru", selected)

    assert prompt.startswith("Главный пользовательский промпт:")
    assert "Давай подробные объяснения сразу." in prompt
    assert "В конце каждого ответа" in prompt
    assert "Используй прямой, прагматичный и профессиональный тон." in prompt


def test_wizard_config_ru_strings_are_not_mojibake():
    mojibake_markers = ("Ã", "Ð", "Ñ", "â")
    ru_strings = list(_iter_ru_strings(personalization_helpers.WIZARD_CONFIG))

    assert ru_strings
    assert all(not any(marker in value for marker in mojibake_markers) for value in ru_strings)


def test_validate_answers_rejects_unknown_question():
    with pytest.raises(HTTPException):
        personalization_helpers._validate_and_normalize_answers(
            [{"question_id": "unknown", "option_id": "x"}]
        )
