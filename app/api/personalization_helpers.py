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
        "ru": "Персонализируйте ваш AI-опыт",
    },
    "description": {
        "en": "Answer a few questions and we will prepare your main user prompt.",
        "ru": "Ответьте на несколько вопросов, и мы подготовим ваш основной пользовательский промпт.",
    },
    "questions": [
        {
            "id": "answer_depth",
            "title": {
                "en": "Do you want detailed explanations right away?",
                "ru": "Нужны подробные объяснения сразу?",
            },
            "options": [
                {
                    "id": "detailed_first",
                    "label": {
                        "en": "Yes, detailed first",
                        "ru": "Да, сразу подробно",
                    },
                    "fragment": {
                        "en": "Give detailed explanations right away. Ask clarifying questions only when critical context is missing.",
                        "ru": "Давай подробные объяснения сразу. Уточняющие вопросы задавай только если не хватает критичного контекста.",
                    },
                },
                {
                    "id": "clarify_first",
                    "label": {
                        "en": "Ask clarifying questions first",
                        "ru": "Сначала уточняющие вопросы",
                    },
                    "fragment": {
                        "en": "Before giving a full answer, ask concise clarifying questions when the request is ambiguous.",
                        "ru": "Перед полным ответом задавай краткие уточняющие вопросы, если запрос неоднозначный.",
                    },
                },
            ],
        },
        {
            "id": "follow_up",
            "title": {
                "en": "Should AI end replies with a follow-up question about next actions?",
                "ru": "Нужно ли AI заканчивать ответы вопросом о следующих действиях?",
            },
            "options": [
                {
                    "id": "ask_follow_up",
                    "label": {
                        "en": "Yes, ask follow-up questions",
                        "ru": "Да, задавать вопрос в конце",
                    },
                    "fragment": {
                        "en": "At the end of each response, ask one short follow-up question about the next useful action.",
                        "ru": "В конце каждого ответа задавай один короткий вопрос о следующем полезном действии.",
                    },
                },
                {
                    "id": "no_follow_up",
                    "label": {
                        "en": "No follow-up questions",
                        "ru": "Нет, без вопросов в конце",
                    },
                    "fragment": {
                        "en": "Do not end responses with a follow-up question unless the user explicitly asks for suggestions.",
                        "ru": "Не завершай ответы вопросом, если пользователь явно не просит подсказки по дальнейшим шагам.",
                    },
                },
            ],
        },
        {
            "id": "tone",
            "title": {
                "en": "Which tone should AI use?",
                "ru": "Какой тон должен использовать AI?",
            },
            "options": [
                {
                    "id": "direct_professional",
                    "label": {
                        "en": "Direct and professional",
                        "ru": "Прямой и профессиональный",
                    },
                    "fragment": {
                        "en": "Use a direct, pragmatic, and professional tone. Avoid fluff.",
                        "ru": "Используй прямой, прагматичный и профессиональный тон. Избегай воды.",
                    },
                },
                {
                    "id": "friendly_calm",
                    "label": {
                        "en": "Friendly and calm",
                        "ru": "Дружелюбный и спокойный",
                    },
                    "fragment": {
                        "en": "Use a friendly, calm tone while staying concise and practical.",
                        "ru": "Используй дружелюбный и спокойный тон, оставаясь кратким и практичным.",
                    },
                },
            ],
        },
        {
            "id": "formatting",
            "title": {
                "en": "How should AI structure responses?",
                "ru": "Как AI должен структурировать ответы?",
            },
            "options": [
                {
                    "id": "structured",
                    "label": {
                        "en": "Structured with bullets",
                        "ru": "Структурно, с пунктами",
                    },
                    "fragment": {
                        "en": "Prefer structured responses with short sections and bullet points when it improves clarity.",
                        "ru": "Предпочитай структурные ответы с короткими секциями и списками, когда это повышает ясность.",
                    },
                },
                {
                    "id": "compact_prose",
                    "label": {
                        "en": "Compact prose",
                        "ru": "Короткий связный текст",
                    },
                    "fragment": {
                        "en": "Prefer compact prose by default. Use lists only when they are clearly necessary.",
                        "ru": "По умолчанию отвечай коротким связным текстом. Списки используй только когда это действительно нужно.",
                    },
                },
            ],
        },
        {
            "id": "proactivity",
            "title": {
                "en": "How proactive should AI be?",
                "ru": "Насколько проактивным должен быть AI?",
            },
            "options": [
                {
                    "id": "proactive",
                    "label": {
                        "en": "Proactive with next steps",
                        "ru": "Проактивный, со следующими шагами",
                    },
                    "fragment": {
                        "en": "When relevant, propose concrete next steps and include implementation-ready details.",
                        "ru": "Когда уместно, предлагай конкретные следующие шаги и давай детали, готовые к применению.",
                    },
                },
                {
                    "id": "on_request",
                    "label": {
                        "en": "Only on explicit request",
                        "ru": "Только по явному запросу",
                    },
                    "fragment": {
                        "en": "Do not suggest extra next steps unless the user explicitly asks for them.",
                        "ru": "Не предлагай дополнительные шаги, если пользователь прямо об этом не просит.",
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
        prefix = "Главный пользовательский промпт:\n"
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
