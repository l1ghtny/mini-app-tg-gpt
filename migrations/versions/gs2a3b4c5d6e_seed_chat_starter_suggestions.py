"""seed chat starter suggestions

Revision ID: gs2a3b4c5d6e
Revises: gs1a2b3c4d5e
Create Date: 2026-05-24 12:45:00.000000

"""
from typing import Sequence, Union
import uuid
from datetime import datetime, UTC

from alembic import op
import sqlalchemy as sa


revision: str = "gs2a3b4c5d6e"
down_revision: Union[str, None] = "gs1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_NS = uuid.UUID("b234f3cc-7dd5-4b26-9b75-0dc31961af73")


def _sid(label: str) -> str:
    return str(uuid.uuid5(_SEED_NS, label))


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def upgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    t = sa.Table("chat_starter_suggestion", meta, autoload_with=bind)

    rows = [
        {"id": _sid("en-1"), "language": "en", "text": "Summarize this article and list key takeaways.", "is_active": True, "sort_index": 10, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-2"), "language": "en", "text": "Help me draft a polite follow-up email to a recruiter.", "is_active": True, "sort_index": 20, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-3"), "language": "en", "text": "Give me a step-by-step plan to learn SQL in 30 days.", "is_active": True, "sort_index": 30, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-4"), "language": "en", "text": "Rewrite this message to sound more professional.", "is_active": True, "sort_index": 40, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-5"), "language": "en", "text": "Generate 10 startup name ideas for an AI study app.", "is_active": True, "sort_index": 50, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-6"), "language": "en", "text": "Create a weekly workout plan for beginners at home.", "is_active": True, "sort_index": 60, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-7"), "language": "en", "text": "Explain this Python error and how to fix it.", "is_active": True, "sort_index": 70, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-8"), "language": "en", "text": "Give me 5 dinner recipes with chicken and rice.", "is_active": True, "sort_index": 80, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-9"), "language": "en", "text": "Turn my rough notes into a clean meeting summary.", "is_active": True, "sort_index": 90, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-10"), "language": "en", "text": "Plan a 3-day trip itinerary for London on a budget.", "is_active": True, "sort_index": 100, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-11"), "language": "en", "text": "Compare React and Vue for a small startup project.", "is_active": True, "sort_index": 110, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-12"), "language": "en", "text": "Create a daily study schedule for exam preparation.", "is_active": True, "sort_index": 120, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-13"), "language": "en", "text": "Suggest 15 content ideas for a tech Instagram account.", "is_active": True, "sort_index": 130, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-14"), "language": "en", "text": "Help me write a short product description for this item.", "is_active": True, "sort_index": 140, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-15"), "language": "en", "text": "Explain this SQL query and optimize it for speed.", "is_active": True, "sort_index": 150, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("en-16"), "language": "en", "text": "Draft a weekly meal plan with high-protein options.", "is_active": True, "sort_index": 160, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-1"), "language": "ru", "text": "Составь план изучения английского на 3 месяца.", "is_active": True, "sort_index": 10, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-2"), "language": "ru", "text": "Помоги написать вежливый ответ клиенту с отказом.", "is_active": True, "sort_index": 20, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-3"), "language": "ru", "text": "Объясни простыми словами, что такое Docker.", "is_active": True, "sort_index": 30, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-4"), "language": "ru", "text": "Сделай краткий конспект этого текста по пунктам.", "is_active": True, "sort_index": 40, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-5"), "language": "ru", "text": "Придумай 10 идей контента для Telegram-канала.", "is_active": True, "sort_index": 50, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-6"), "language": "ru", "text": "Собери список вопросов для собеседования на junior frontend.", "is_active": True, "sort_index": 60, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-7"), "language": "ru", "text": "Предложи недельное меню с бюджетом до 5000 рублей.", "is_active": True, "sort_index": 70, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-8"), "language": "ru", "text": "Перепиши этот абзац в более деловом стиле.", "is_active": True, "sort_index": 80, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-9"), "language": "ru", "text": "Сделай краткое резюме встречи по этим заметкам.", "is_active": True, "sort_index": 90, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-10"), "language": "ru", "text": "Составь маршрут поездки на 3 дня по Санкт-Петербургу.", "is_active": True, "sort_index": 100, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-11"), "language": "ru", "text": "Сравни React и Vue для небольшого продукта.", "is_active": True, "sort_index": 110, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-12"), "language": "ru", "text": "Собери ежедневный график подготовки к экзамену.", "is_active": True, "sort_index": 120, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-13"), "language": "ru", "text": "Придумай 15 идей постов для IT-блога.", "is_active": True, "sort_index": 130, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-14"), "language": "ru", "text": "Помоги написать описание товара для маркетплейса.", "is_active": True, "sort_index": 140, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-15"), "language": "ru", "text": "Объясни этот SQL-запрос и как его ускорить.", "is_active": True, "sort_index": 150, "created_at": _now(), "updated_at": _now()},
        {"id": _sid("ru-16"), "language": "ru", "text": "Составь меню на неделю с упором на белок.", "is_active": True, "sort_index": 160, "created_at": _now(), "updated_at": _now()},
    ]

    for row in rows:
        existing = bind.execute(sa.select(t.c.id).where(t.c.id == row["id"])).first()
        if existing is None:
            bind.execute(t.insert().values(**row))


def downgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    t = sa.Table("chat_starter_suggestion", meta, autoload_with=bind)
    bind.execute(
        t.delete().where(
            t.c.id.in_([
                _sid("en-1"), _sid("en-2"), _sid("en-3"), _sid("en-4"),
                _sid("en-5"), _sid("en-6"), _sid("en-7"), _sid("en-8"),
                _sid("en-9"), _sid("en-10"), _sid("en-11"), _sid("en-12"),
                _sid("en-13"), _sid("en-14"), _sid("en-15"), _sid("en-16"),
                _sid("ru-1"), _sid("ru-2"), _sid("ru-3"), _sid("ru-4"),
                _sid("ru-5"), _sid("ru-6"), _sid("ru-7"), _sid("ru-8"),
                _sid("ru-9"), _sid("ru-10"), _sid("ru-11"), _sid("ru-12"),
                _sid("ru-13"), _sid("ru-14"), _sid("ru-15"), _sid("ru-16"),
            ])
        )
    )
