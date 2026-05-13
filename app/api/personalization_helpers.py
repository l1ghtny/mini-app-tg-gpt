from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppUser, UserPersonalization
from app.schemas.personalization import (
    PersonalizationComposeRequest,
    PersonalizationComposeResponse,
    PersonalizationDismissResponse,
    PersonalizationProfileResponse,
    PersonalizationSkipResponse,
    PersonalizationWizardResponse,
    UpdatePersonalizationRequest,
    WizardOptionResponse,
    WizardQuestionResponse,
)

PERSONALIZATION_WIZARD_VERSION = "2026-05-13"
PERSONALIZATION_DISMISS_COOLDOWN_DAYS = 3


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


WIZARD_CONFIG: dict[str, Any] = {
    "version": PERSONALIZATION_WIZARD_VERSION,
    "title": {
        "en": "Personalize your AI experience",
        "ru": "ÐŸÐµÑ€ÑÐ¾Ð½Ð°Ð»Ð¸Ð·Ð¸Ñ€ÑƒÐ¹Ñ‚Ðµ Ð²Ð°Ñˆ AI-Ð¾Ð¿Ñ‹Ñ‚",
    },
    "description": {
        "en": "Answer a few questions and we will prepare your main user prompt.",
        "ru": "ÐžÑ‚Ð²ÐµÑ‚ÑŒÑ‚Ðµ Ð½Ð° Ð½ÐµÑÐºÐ¾Ð»ÑŒÐºÐ¾ Ð²Ð¾Ð¿Ñ€Ð¾ÑÐ¾Ð², Ð¸ Ð¼Ñ‹ Ð¿Ð¾Ð´Ð³Ð¾Ñ‚Ð¾Ð²Ð¸Ð¼ Ð²Ð°Ñˆ Ð¾ÑÐ½Ð¾Ð²Ð½Ð¾Ð¹ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŒÑÐºÐ¸Ð¹ Ð¿Ñ€Ð¾Ð¼Ð¿Ñ‚.",
    },
    "questions": [
        {
            "id": "answer_depth",
            "title": {
                "en": "Do you want detailed explanations right away?",
                "ru": "ÐÑƒÐ¶Ð½Ñ‹ Ð¿Ð¾Ð´Ñ€Ð¾Ð±Ð½Ñ‹Ðµ Ð¾Ð±ÑŠÑÑÐ½ÐµÐ½Ð¸Ñ ÑÑ€Ð°Ð·Ñƒ?",
            },
            "options": [
                {
                    "id": "detailed_first",
                    "label": {
                        "en": "Yes, detailed first",
                        "ru": "Ð”Ð°, ÑÑ€Ð°Ð·Ñƒ Ð¿Ð¾Ð´Ñ€Ð¾Ð±Ð½Ð¾",
                    },
                    "fragment": {
                        "en": "Give detailed explanations right away. Ask clarifying questions only when critical context is missing.",
                        "ru": "Ð”Ð°Ð²Ð°Ð¹ Ð¿Ð¾Ð´Ñ€Ð¾Ð±Ð½Ñ‹Ðµ Ð¾Ð±ÑŠÑÑÐ½ÐµÐ½Ð¸Ñ ÑÑ€Ð°Ð·Ñƒ. Ð£Ñ‚Ð¾Ñ‡Ð½ÑÑŽÑ‰Ð¸Ðµ Ð²Ð¾Ð¿Ñ€Ð¾ÑÑ‹ Ð·Ð°Ð´Ð°Ð²Ð°Ð¹ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÐµÑÐ»Ð¸ Ð½Ðµ Ñ…Ð²Ð°Ñ‚Ð°ÐµÑ‚ ÐºÑ€Ð¸Ñ‚Ð¸Ñ‡Ð½Ð¾Ð³Ð¾ ÐºÐ¾Ð½Ñ‚ÐµÐºÑÑ‚Ð°.",
                    },
                },
                {
                    "id": "clarify_first",
                    "label": {
                        "en": "Ask clarifying questions first",
                        "ru": "Ð¡Ð½Ð°Ñ‡Ð°Ð»Ð° ÑƒÑ‚Ð¾Ñ‡Ð½ÑÑŽÑ‰Ð¸Ðµ Ð²Ð¾Ð¿Ñ€Ð¾ÑÑ‹",
                    },
                    "fragment": {
                        "en": "Before giving a full answer, ask concise clarifying questions when the request is ambiguous.",
                        "ru": "ÐŸÐµÑ€ÐµÐ´ Ð¿Ð¾Ð»Ð½Ñ‹Ð¼ Ð¾Ñ‚Ð²ÐµÑ‚Ð¾Ð¼ Ð·Ð°Ð´Ð°Ð²Ð°Ð¹ ÐºÑ€Ð°Ñ‚ÐºÐ¸Ðµ ÑƒÑ‚Ð¾Ñ‡Ð½ÑÑŽÑ‰Ð¸Ðµ Ð²Ð¾Ð¿Ñ€Ð¾ÑÑ‹, ÐµÑÐ»Ð¸ Ð·Ð°Ð¿Ñ€Ð¾Ñ Ð½ÐµÐ¾Ð´Ð½Ð¾Ð·Ð½Ð°Ñ‡Ð½Ñ‹Ð¹.",
                    },
                },
            ],
        },
        {
            "id": "follow_up",
            "title": {
                "en": "Should AI end replies with a follow-up question about next actions?",
                "ru": "ÐÑƒÐ¶Ð½Ð¾ Ð»Ð¸ Ð·Ð°ÐºÐ°Ð½Ñ‡Ð¸Ð²Ð°Ñ‚ÑŒ Ð¾Ñ‚Ð²ÐµÑ‚Ñ‹ Ð²Ð¾Ð¿Ñ€Ð¾ÑÐ¾Ð¼ Ð¾ ÑÐ»ÐµÐ´ÑƒÑŽÑ‰Ð¸Ñ… Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸ÑÑ…?",
            },
            "options": [
                {
                    "id": "ask_follow_up",
                    "label": {
                        "en": "Yes, ask follow-up questions",
                        "ru": "Ð”Ð°, Ð·Ð°Ð´Ð°Ð²Ð°Ñ‚ÑŒ Ð²Ð¾Ð¿Ñ€Ð¾Ñ Ð² ÐºÐ¾Ð½Ñ†Ðµ",
                    },
                    "fragment": {
                        "en": "At the end of each response, ask one short follow-up question about the next useful action.",
                        "ru": "Ð’ ÐºÐ¾Ð½Ñ†Ðµ ÐºÐ°Ð¶Ð´Ð¾Ð³Ð¾ Ð¾Ñ‚Ð²ÐµÑ‚Ð° Ð·Ð°Ð´Ð°Ð²Ð°Ð¹ Ð¾Ð´Ð¸Ð½ ÐºÐ¾Ñ€Ð¾Ñ‚ÐºÐ¸Ð¹ Ð²Ð¾Ð¿Ñ€Ð¾Ñ Ð¾ ÑÐ»ÐµÐ´ÑƒÑŽÑ‰ÐµÐ¼ Ð¿Ð¾Ð»ÐµÐ·Ð½Ð¾Ð¼ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ð¸.",
                    },
                },
                {
                    "id": "no_follow_up",
                    "label": {
                        "en": "No follow-up questions",
                        "ru": "ÐÐµÑ‚, Ð±ÐµÐ· Ð²Ð¾Ð¿Ñ€Ð¾ÑÐ¾Ð² Ð² ÐºÐ¾Ð½Ñ†Ðµ",
                    },
                    "fragment": {
                        "en": "Do not end responses with a follow-up question unless the user explicitly asks for suggestions.",
                        "ru": "ÐÐµ Ð·Ð°Ð²ÐµÑ€ÑˆÐ°Ð¹ Ð¾Ñ‚Ð²ÐµÑ‚Ñ‹ Ð²Ð¾Ð¿Ñ€Ð¾ÑÐ¾Ð¼, ÐµÑÐ»Ð¸ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŒ ÑÐ²Ð½Ð¾ Ð½Ðµ Ð¿Ñ€Ð¾ÑÐ¸Ñ‚ Ð¿Ð¾Ð´ÑÐºÐ°Ð·ÐºÐ¸ Ð¿Ð¾ Ð´Ð°Ð»ÑŒÐ½ÐµÐ¹ÑˆÐ¸Ð¼ ÑˆÐ°Ð³Ð°Ð¼.",
                    },
                },
            ],
        },
        {
            "id": "tone",
            "title": {
                "en": "Which tone should AI use?",
                "ru": "ÐšÐ°ÐºÐ¾Ð¹ Ñ‚Ð¾Ð½ Ð´Ð¾Ð»Ð¶ÐµÐ½ Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÑŒ AI?",
            },
            "options": [
                {
                    "id": "direct_professional",
                    "label": {
                        "en": "Direct and professional",
                        "ru": "ÐŸÑ€ÑÐ¼Ð¾Ð¹ Ð¸ Ð¿Ñ€Ð¾Ñ„ÐµÑÑÐ¸Ð¾Ð½Ð°Ð»ÑŒÐ½Ñ‹Ð¹",
                    },
                    "fragment": {
                        "en": "Use a direct, pragmatic, and professional tone. Avoid fluff.",
                        "ru": "Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐ¹ Ð¿Ñ€ÑÐ¼Ð¾Ð¹, Ð¿Ñ€Ð°Ð³Ð¼Ð°Ñ‚Ð¸Ñ‡Ð½Ñ‹Ð¹ Ð¸ Ð¿Ñ€Ð¾Ñ„ÐµÑÑÐ¸Ð¾Ð½Ð°Ð»ÑŒÐ½Ñ‹Ð¹ Ñ‚Ð¾Ð½. Ð˜Ð·Ð±ÐµÐ³Ð°Ð¹ Ð²Ð¾Ð´Ñ‹.",
                    },
                },
                {
                    "id": "friendly_calm",
                    "label": {
                        "en": "Friendly and calm",
                        "ru": "Ð”Ñ€ÑƒÐ¶ÐµÐ»ÑŽÐ±Ð½Ñ‹Ð¹ Ð¸ ÑÐ¿Ð¾ÐºÐ¾Ð¹Ð½Ñ‹Ð¹",
                    },
                    "fragment": {
                        "en": "Use a friendly, calm tone while staying concise and practical.",
                        "ru": "Ð˜ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐ¹ Ð´Ñ€ÑƒÐ¶ÐµÐ»ÑŽÐ±Ð½Ñ‹Ð¹ Ð¸ ÑÐ¿Ð¾ÐºÐ¾Ð¹Ð½Ñ‹Ð¹ Ñ‚Ð¾Ð½, Ð¾ÑÑ‚Ð°Ð²Ð°ÑÑÑŒ ÐºÑ€Ð°Ñ‚ÐºÐ¸Ð¼ Ð¸ Ð¿Ñ€Ð°ÐºÑ‚Ð¸Ñ‡Ð½Ñ‹Ð¼.",
                    },
                },
            ],
        },
        {
            "id": "formatting",
            "title": {
                "en": "How should AI structure responses?",
                "ru": "ÐšÐ°Ðº ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð¸Ñ€Ð¾Ð²Ð°Ñ‚ÑŒ Ð¾Ñ‚Ð²ÐµÑ‚Ñ‹?",
            },
            "options": [
                {
                    "id": "structured",
                    "label": {
                        "en": "Structured with bullets",
                        "ru": "Ð¡Ñ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð½Ð¾, Ñ Ð¿ÑƒÐ½ÐºÑ‚Ð°Ð¼Ð¸",
                    },
                    "fragment": {
                        "en": "Prefer structured responses with short sections and bullet points when it improves clarity.",
                        "ru": "ÐŸÑ€ÐµÐ´Ð¿Ð¾Ñ‡Ð¸Ñ‚Ð°Ð¹ ÑÑ‚Ñ€ÑƒÐºÑ‚ÑƒÑ€Ð½Ñ‹Ðµ Ð¾Ñ‚Ð²ÐµÑ‚Ñ‹ Ñ ÐºÐ¾Ñ€Ð¾Ñ‚ÐºÐ¸Ð¼Ð¸ ÑÐµÐºÑ†Ð¸ÑÐ¼Ð¸ Ð¸ ÑÐ¿Ð¸ÑÐºÐ°Ð¼Ð¸, ÐºÐ¾Ð³Ð´Ð° ÑÑ‚Ð¾ Ð¿Ð¾Ð²Ñ‹ÑˆÐ°ÐµÑ‚ ÑÑÐ½Ð¾ÑÑ‚ÑŒ.",
                    },
                },
                {
                    "id": "compact_prose",
                    "label": {
                        "en": "Compact prose",
                        "ru": "ÐšÐ¾Ñ€Ð¾Ñ‚ÐºÐ¸Ð¹ ÑÐ²ÑÐ·Ð½Ñ‹Ð¹ Ñ‚ÐµÐºÑÑ‚",
                    },
                    "fragment": {
                        "en": "Prefer compact prose by default. Use lists only when they are clearly necessary.",
                        "ru": "ÐŸÐ¾ ÑƒÐ¼Ð¾Ð»Ñ‡Ð°Ð½Ð¸ÑŽ Ð¾Ñ‚Ð²ÐµÑ‡Ð°Ð¹ ÐºÐ¾Ñ€Ð¾Ñ‚ÐºÐ¸Ð¼ ÑÐ²ÑÐ·Ð½Ñ‹Ð¼ Ñ‚ÐµÐºÑÑ‚Ð¾Ð¼. Ð¡Ð¿Ð¸ÑÐºÐ¸ Ð¸ÑÐ¿Ð¾Ð»ÑŒÐ·ÑƒÐ¹ Ñ‚Ð¾Ð»ÑŒÐºÐ¾ ÐºÐ¾Ð³Ð´Ð° ÑÑ‚Ð¾ Ð´ÐµÐ¹ÑÑ‚Ð²Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ð¾ Ð½ÑƒÐ¶Ð½Ð¾.",
                    },
                },
            ],
        },
        {
            "id": "proactivity",
            "title": {
                "en": "How proactive should AI be?",
                "ru": "ÐÐ°ÑÐºÐ¾Ð»ÑŒÐºÐ¾ Ð¿Ñ€Ð¾Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ð¼ Ð´Ð¾Ð»Ð¶ÐµÐ½ Ð±Ñ‹Ñ‚ÑŒ AI?",
            },
            "options": [
                {
                    "id": "proactive",
                    "label": {
                        "en": "Proactive with next steps",
                        "ru": "ÐŸÑ€Ð¾Ð°ÐºÑ‚Ð¸Ð²Ð½Ñ‹Ð¹, Ñ next steps",
                    },
                    "fragment": {
                        "en": "When relevant, propose concrete next steps and include implementation-ready details.",
                        "ru": "ÐšÐ¾Ð³Ð´Ð° ÑƒÐ¼ÐµÑÑ‚Ð½Ð¾, Ð¿Ñ€ÐµÐ´Ð»Ð°Ð³Ð°Ð¹ ÐºÐ¾Ð½ÐºÑ€ÐµÑ‚Ð½Ñ‹Ðµ ÑÐ»ÐµÐ´ÑƒÑŽÑ‰Ð¸Ðµ ÑˆÐ°Ð³Ð¸ Ð¸ Ð´Ð°Ð²Ð°Ð¹ Ð´ÐµÑ‚Ð°Ð»Ð¸, Ð³Ð¾Ñ‚Ð¾Ð²Ñ‹Ðµ Ðº Ð¿Ñ€Ð¸Ð¼ÐµÐ½ÐµÐ½Ð¸ÑŽ.",
                    },
                },
                {
                    "id": "on_request",
                    "label": {
                        "en": "Only on explicit request",
                        "ru": "Ð¢Ð¾Ð»ÑŒÐºÐ¾ Ð¿Ð¾ ÑÐ²Ð½Ð¾Ð¼Ñƒ Ð·Ð°Ð¿Ñ€Ð¾ÑÑƒ",
                    },
                    "fragment": {
                        "en": "Do not suggest extra next steps unless the user explicitly asks for them.",
                        "ru": "ÐÐµ Ð¿Ñ€ÐµÐ´Ð»Ð°Ð³Ð°Ð¹ Ð´Ð¾Ð¿Ð¾Ð»Ð½Ð¸Ñ‚ÐµÐ»ÑŒÐ½Ñ‹Ðµ ÑˆÐ°Ð³Ð¸, ÐµÑÐ»Ð¸ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŒ Ð¿Ñ€ÑÐ¼Ð¾ Ð¾Ð± ÑÑ‚Ð¾Ð¼ Ð½Ðµ Ð¿Ñ€Ð¾ÑÐ¸Ñ‚.",
                    },
                },
            ],
        },
    ],
}


def _next_prompt_at(dismissed_at: datetime | None) -> datetime | None:
    if dismissed_at is None:
        return None
    return dismissed_at + timedelta(days=PERSONALIZATION_DISMISS_COOLDOWN_DAYS)


def _show_prompt(personalization: UserPersonalization | None, now: datetime) -> bool:
    if personalization and personalization.completed_at is not None:
        return False
    dismissed_at = personalization.dismissed_at if personalization else None
    next_prompt_at = _next_prompt_at(dismissed_at)
    if next_prompt_at is None:
        return True
    return now >= next_prompt_at


def _validate_and_normalize_answers(answers: list[dict[str, str]]) -> dict[str, str]:
    by_question: dict[str, str] = {}
    valid_options_by_question: dict[str, set[str]] = {
        question["id"]: {option["id"] for option in question["options"]}
        for question in WIZARD_CONFIG["questions"]
    }

    for answer in answers:
        question_id = answer.get("question_id", "").strip()
        option_id = answer.get("option_id", "").strip()
        if not question_id or not option_id:
            raise HTTPException(status_code=400, detail="Invalid wizard answer payload")
        allowed_options = valid_options_by_question.get(question_id)
        if not allowed_options:
            raise HTTPException(status_code=400, detail=f"Unknown question_id: {question_id}")
        if option_id not in allowed_options:
            raise HTTPException(status_code=400, detail=f"Unknown option_id '{option_id}' for question '{question_id}'")
        by_question[question_id] = option_id

    if not by_question:
        raise HTTPException(status_code=400, detail="At least one wizard answer is required")

    return by_question


def _compose_prompt(language: str, selected: dict[str, str]) -> str:
    lang = "ru" if language == "ru" else "en"
    fragments: list[str] = []

    for question in WIZARD_CONFIG["questions"]:
        selected_option_id = selected.get(question["id"])
        if not selected_option_id:
            continue
        option = next((o for o in question["options"] if o["id"] == selected_option_id), None)
        if not option:
            continue
        fragment = option["fragment"].get(lang) or option["fragment"]["en"]
        fragments.append(fragment.strip())

    if not fragments:
        raise HTTPException(status_code=400, detail="No valid prompt fragments were selected")

    if lang == "ru":
        prefix = "Ð“Ð»Ð°Ð²Ð½Ñ‹Ð¹ Ð¿Ð¾Ð»ÑŒÐ·Ð¾Ð²Ð°Ñ‚ÐµÐ»ÑŒÑÐºÐ¸Ð¹ Ð¿Ñ€Ð¾Ð¼Ð¿Ñ‚:\n"
    else:
        prefix = "Main user prompt:\n"

    return prefix + "\n".join(f"- {fragment}" for fragment in fragments)


async def _get_or_create_personalization(
    session: AsyncSession,
    user_id,
) -> UserPersonalization:
    personalization = await session.get(UserPersonalization, user_id)
    if personalization:
        return personalization

    personalization = UserPersonalization(user_id=user_id)
    session.add(personalization)
    await session.flush()
    return personalization


async def get_personalization_profile(session: AsyncSession, current_user: AppUser) -> PersonalizationProfileResponse:
    user = await session.get(AppUser, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    personalization = await session.get(UserPersonalization, user.id)

    now = _utcnow_naive()
    dismissed_at = personalization.dismissed_at if personalization else None
    completed_at = personalization.completed_at if personalization else None
    updated_at = personalization.updated_at if personalization else None
    next_prompt_at = _next_prompt_at(dismissed_at)
    return PersonalizationProfileResponse(
        main_user_prompt=user.default_prompt or "",
        personalization_completed_at=completed_at,
        personalization_dismissed_at=dismissed_at,
        personalization_updated_at=updated_at,
        show_personalization_prompt=_show_prompt(personalization, now),
        next_prompt_at=next_prompt_at,
        wizard_version=PERSONALIZATION_WIZARD_VERSION,
    )


async def update_personalization_profile(
    session: AsyncSession,
    current_user: AppUser,
    request: UpdatePersonalizationRequest,
) -> PersonalizationProfileResponse:
    user = await session.get(AppUser, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    prompt = request.main_user_prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="main_user_prompt cannot be empty")

    now = _utcnow_naive()
    personalization = await _get_or_create_personalization(session, user.id)
    user.default_prompt = prompt
    personalization.updated_at = now
    personalization.completed_at = now
    personalization.dismissed_at = None

    if request.answers is not None:
        personalization.answers = {
            "version": PERSONALIZATION_WIZARD_VERSION,
            "source": request.source,
            "answers": request.answers,
        }

    session.add(user)
    session.add(personalization)
    await session.commit()
    await session.refresh(user)

    return await get_personalization_profile(session, user)


async def dismiss_personalization_prompt(
    session: AsyncSession,
    current_user: AppUser,
) -> PersonalizationDismissResponse:
    user = await session.get(AppUser, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = _utcnow_naive()
    personalization = await _get_or_create_personalization(session, user.id)
    personalization.dismissed_at = now
    session.add(personalization)
    await session.commit()

    return PersonalizationDismissResponse(
        status="ok",
        next_prompt_at=now + timedelta(days=PERSONALIZATION_DISMISS_COOLDOWN_DAYS),
    )


async def skip_personalization_prompt(
    session: AsyncSession,
    current_user: AppUser,
) -> PersonalizationSkipResponse:
    user = await session.get(AppUser, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = _utcnow_naive()
    personalization = await _get_or_create_personalization(session, user.id)
    personalization.completed_at = now
    personalization.updated_at = now
    personalization.dismissed_at = None
    session.add(personalization)
    await session.commit()

    return PersonalizationSkipResponse(status="ok")


async def get_personalization_wizard() -> PersonalizationWizardResponse:
    questions: list[WizardQuestionResponse] = []
    for question in WIZARD_CONFIG["questions"]:
        options = [
            WizardOptionResponse(
                id=option["id"],
                label=option["label"]["en"],
                label_ru=option["label"]["ru"],
            )
            for option in question["options"]
        ]
        questions.append(
            WizardQuestionResponse(
                id=question["id"],
                title=question["title"]["en"],
                title_ru=question["title"]["ru"],
                options=options,
            )
        )

    return PersonalizationWizardResponse(
        version=WIZARD_CONFIG["version"],
        title=WIZARD_CONFIG["title"]["en"],
        title_ru=WIZARD_CONFIG["title"]["ru"],
        description=WIZARD_CONFIG["description"]["en"],
        description_ru=WIZARD_CONFIG["description"]["ru"],
        questions=questions,
    )


async def compose_personalization_prompt(request: PersonalizationComposeRequest) -> PersonalizationComposeResponse:
    selected = _validate_and_normalize_answers([answer.model_dump() for answer in request.answers])
    composed = _compose_prompt(request.language, selected)
    return PersonalizationComposeResponse(
        composed_prompt=composed,
        answers=selected,
        version=PERSONALIZATION_WIZARD_VERSION,
    )
