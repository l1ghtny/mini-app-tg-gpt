"""seed model handler catalog data

Revision ID: e31a7f88b9d2
Revises: c2ab7f1d9e40
Create Date: 2026-05-03 00:15:00.000000

"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = "e31a7f88b9d2"
down_revision: Union[str, Sequence[str], None] = "c2ab7f1d9e40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("34e7db20-59d0-4ee6-8189-83e2d12f4685")


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


TEXT_MODELS = [
    {
        "provider": "OpenAI",
        "model_name": "gpt-5-nano",
        "display_name": "Nano",
        "display_name_ru": "Нано",
        "tagline": "Lowest-cost, ultra-fast answers",
        "tagline_ru": "Самые дешевые и сверхбыстрые ответы",
        "description": "Best for lightweight prompts, quick rewrites, and high-throughput chat.",
        "description_ru": "Лучше всего для простых запросов, быстрых правок и массового чата.",
        "best_for": ["Quick chat", "Simple rewrites", "High-volume requests"],
        "best_for_ru": ["Быстрый чат", "Простые переформулировки", "Большой поток запросов"],
        "not_great_for": ["Deep reasoning", "Complex coding", "Long analyses"],
        "not_great_for_ru": ["Глубокие рассуждения", "Сложное программирование", "Длинная аналитика"],
        "speed": "fast",
        "intelligence": 1,
        "context_window": 128000,
        "supports": {
            "vision": False,
            "web_search": True,
            "file_search": False,
            "image_gen": False,
            "reasoning": False,
        },
        "tier_required": {"slug": "free", "min_rank": 0},
        "badges": [],
        "credit_cost_hint": Decimal("1.0"),
        "sort_index": 10,
    },
    {
        "provider": "OpenAI",
        "model_name": "gpt-5-mini",
        "display_name": "Fast",
        "display_name_ru": "Быстрый",
        "tagline": "Quick everyday answers",
        "tagline_ru": "Быстрые ответы на каждый день",
        "description": "Best for short questions, drafting, and quick rewrites.",
        "description_ru": "Лучше всего для коротких вопросов, черновиков и быстрых правок.",
        "best_for": ["Quick chat", "Summaries", "Translations"],
        "best_for_ru": ["Быстрый чат", "Саммари", "Переводы"],
        "not_great_for": ["Long reasoning", "Complex code"],
        "not_great_for_ru": ["Долгие рассуждения", "Сложный код"],
        "speed": "fast",
        "intelligence": 2,
        "context_window": 128000,
        "supports": {
            "vision": True,
            "web_search": True,
            "file_search": False,
            "image_gen": False,
            "reasoning": False,
        },
        "tier_required": {"slug": "basic", "min_rank": 1},
        "badges": ["recommended"],
        "credit_cost_hint": Decimal("1.5"),
        "sort_index": 20,
    },
    {
        "provider": "OpenAI",
        "model_name": "gpt-5.2",
        "display_name": "Balanced",
        "display_name_ru": "Сбалансированный",
        "tagline": "Reliable depth for serious tasks",
        "tagline_ru": "Надежная глубина для серьезных задач",
        "description": "Great default for coding, analysis, and detailed multi-step responses.",
        "description_ru": "Хороший базовый выбор для кода, аналитики и детальных многошаговых ответов.",
        "best_for": ["Coding", "Analysis", "Structured outputs"],
        "best_for_ru": ["Программирование", "Аналитика", "Структурированные ответы"],
        "not_great_for": ["Ultra-low latency tasks"],
        "not_great_for_ru": ["Задачи с минимальной задержкой"],
        "speed": "medium",
        "intelligence": 4,
        "context_window": 128000,
        "supports": {
            "vision": True,
            "web_search": True,
            "file_search": True,
            "image_gen": False,
            "reasoning": True,
        },
        "tier_required": {"slug": "pro", "min_rank": 2},
        "badges": ["pro"],
        "credit_cost_hint": Decimal("3.0"),
        "sort_index": 30,
    },
    {
        "provider": "OpenAI",
        "model_name": "gpt-5.5",
        "display_name": "Flagship",
        "display_name_ru": "Флагман",
        "tagline": "Maximum quality for hardest tasks",
        "tagline_ru": "Максимальное качество для самых сложных задач",
        "description": "Best for hardest reasoning, advanced coding, and critical high-accuracy work.",
        "description_ru": "Лучше всего для сложных рассуждений, продвинутого кода и критичных задач с высокой точностью.",
        "best_for": ["Hard reasoning", "Advanced coding", "High-accuracy responses"],
        "best_for_ru": ["Сложные рассуждения", "Продвинутое программирование", "Ответы с высокой точностью"],
        "not_great_for": ["Budget-sensitive bulk tasks"],
        "not_great_for_ru": ["Массовые задачи с жестким бюджетом"],
        "speed": "slow",
        "intelligence": 5,
        "context_window": 128000,
        "supports": {
            "vision": True,
            "web_search": True,
            "file_search": True,
            "image_gen": False,
            "reasoning": True,
        },
        "tier_required": {"slug": "premium", "min_rank": 3},
        "badges": ["new", "pro"],
        "credit_cost_hint": Decimal("5.0"),
        "sort_index": 40,
    },
]


IMAGE_MODELS = [
    {
        "provider": "OpenAI",
        "model_name": "gpt-image-1.5",
        "display_name": "GPT Image 1.5",
        "display_name_ru": "GPT Image 1.5",
        "tagline": "Photoreal images with strong prompt adherence",
        "tagline_ru": "Фотореалистичные изображения с точным следованием промпту",
        "description": "Great for posters, product visuals, and images with legible text.",
        "description_ru": "Отлично подходит для постеров, продуктовых визуалов и изображений с читаемым текстом.",
        "best_for": ["Text in images", "Realistic photos", "Marketing creatives"],
        "best_for_ru": ["Текст на изображениях", "Реалистичные фото", "Маркетинговые креативы"],
        "speed": "medium",
        "tier_required": {"slug": "basic", "min_rank": 1},
        "badges": ["recommended", "pro"],
        "sort_index": 10,
    },
    {
        "provider": "OpenAI",
        "model_name": "gpt-image-2",
        "display_name": "GPT Image 2",
        "display_name_ru": "GPT Image 2",
        "tagline": "Higher-fidelity generation for complex scenes",
        "tagline_ru": "Повышенная детализация для сложных сцен",
        "description": "Best for high-detail compositions and polished final assets.",
        "description_ru": "Лучше всего для детализированных композиций и финальных материалов.",
        "best_for": ["Complex scenes", "High-detail visuals", "Final-quality assets"],
        "best_for_ru": ["Сложные сцены", "Детализированные визуалы", "Финальные материалы"],
        "speed": "slow",
        "tier_required": {"slug": "pro", "min_rank": 2},
        "badges": ["pro"],
        "sort_index": 20,
    },
]


QUALITY_DESCRIPTIONS = {
    "auto": ("Automatic quality selection", "Автоматический выбор качества"),
    "standard": ("Standard quality", "Стандартное качество"),
    "low": ("Fast preview", "Быстрый предпросмотр"),
    "medium": ("Balanced quality", "Сбалансированное качество"),
    "high": ("Crisp final quality", "Максимальная детализация"),
}


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()

    text_model_catalog = sa.Table(
        "text_model_catalog",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("provider", sa.String()),
        sa.Column("model_name", sa.String()),
        sa.Column("display_name", sa.String()),
        sa.Column("display_name_ru", sa.String()),
        sa.Column("tagline", sa.String()),
        sa.Column("tagline_ru", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("description_ru", sa.String()),
        sa.Column("best_for", JSONB),
        sa.Column("best_for_ru", JSONB),
        sa.Column("not_great_for", JSONB),
        sa.Column("not_great_for_ru", JSONB),
        sa.Column("speed", sa.String()),
        sa.Column("intelligence", sa.Integer()),
        sa.Column("context_window", sa.Integer()),
        sa.Column("supports", JSONB),
        sa.Column("tier_required", JSONB),
        sa.Column("badges", JSONB),
        sa.Column("credit_cost_hint", sa.Numeric(18, 6)),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("sort_index", sa.Integer()),
        sa.Column("updated_at", sa.DateTime()),
    )

    image_model_catalog = sa.Table(
        "image_model_catalog",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("provider", sa.String()),
        sa.Column("model_name", sa.String()),
        sa.Column("display_name", sa.String()),
        sa.Column("display_name_ru", sa.String()),
        sa.Column("tagline", sa.String()),
        sa.Column("tagline_ru", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("description_ru", sa.String()),
        sa.Column("best_for", JSONB),
        sa.Column("best_for_ru", JSONB),
        sa.Column("speed", sa.String()),
        sa.Column("tier_required", JSONB),
        sa.Column("badges", JSONB),
        sa.Column("is_active", sa.Boolean()),
        sa.Column("sort_index", sa.Integer()),
        sa.Column("updated_at", sa.DateTime()),
    )

    image_quality_pricing = sa.Table(
        "image_quality_pricing",
        metadata,
        sa.Column("quality", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("description_ru", sa.String()),
    )

    for row in TEXT_MODELS:
        seed_row = {
            "id": _stable_uuid(f"text-model:{row['provider']}:{row['model_name']}"),
            "provider": row["provider"],
            "model_name": row["model_name"],
            "display_name": row["display_name"],
            "display_name_ru": row["display_name_ru"],
            "tagline": row["tagline"],
            "tagline_ru": row["tagline_ru"],
            "description": row["description"],
            "description_ru": row["description_ru"],
            "best_for": row["best_for"],
            "best_for_ru": row["best_for_ru"],
            "not_great_for": row["not_great_for"],
            "not_great_for_ru": row["not_great_for_ru"],
            "speed": row["speed"],
            "intelligence": row["intelligence"],
            "context_window": row["context_window"],
            "supports": row["supports"],
            "tier_required": row["tier_required"],
            "badges": row["badges"],
            "credit_cost_hint": row["credit_cost_hint"],
            "is_active": True,
            "sort_index": row["sort_index"],
        }
        insert_stmt = pg_insert(text_model_catalog).values(**seed_row)
        op.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[text_model_catalog.c.provider, text_model_catalog.c.model_name],
                set_={
                    "display_name": insert_stmt.excluded.display_name,
                    "display_name_ru": insert_stmt.excluded.display_name_ru,
                    "tagline": insert_stmt.excluded.tagline,
                    "tagline_ru": insert_stmt.excluded.tagline_ru,
                    "description": insert_stmt.excluded.description,
                    "description_ru": insert_stmt.excluded.description_ru,
                    "best_for": insert_stmt.excluded.best_for,
                    "best_for_ru": insert_stmt.excluded.best_for_ru,
                    "not_great_for": insert_stmt.excluded.not_great_for,
                    "not_great_for_ru": insert_stmt.excluded.not_great_for_ru,
                    "speed": insert_stmt.excluded.speed,
                    "intelligence": insert_stmt.excluded.intelligence,
                    "context_window": insert_stmt.excluded.context_window,
                    "supports": insert_stmt.excluded.supports,
                    "tier_required": insert_stmt.excluded.tier_required,
                    "badges": insert_stmt.excluded.badges,
                    "credit_cost_hint": insert_stmt.excluded.credit_cost_hint,
                    "is_active": insert_stmt.excluded.is_active,
                    "sort_index": insert_stmt.excluded.sort_index,
                    "updated_at": sa.func.now(),
                },
            )
        )

    for row in IMAGE_MODELS:
        seed_row = {
            "id": _stable_uuid(f"image-model:{row['provider']}:{row['model_name']}"),
            "provider": row["provider"],
            "model_name": row["model_name"],
            "display_name": row["display_name"],
            "display_name_ru": row["display_name_ru"],
            "tagline": row["tagline"],
            "tagline_ru": row["tagline_ru"],
            "description": row["description"],
            "description_ru": row["description_ru"],
            "best_for": row["best_for"],
            "best_for_ru": row["best_for_ru"],
            "speed": row["speed"],
            "tier_required": row["tier_required"],
            "badges": row["badges"],
            "is_active": True,
            "sort_index": row["sort_index"],
        }
        insert_stmt = pg_insert(image_model_catalog).values(**seed_row)
        op.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[image_model_catalog.c.provider, image_model_catalog.c.model_name],
                set_={
                    "display_name": insert_stmt.excluded.display_name,
                    "display_name_ru": insert_stmt.excluded.display_name_ru,
                    "tagline": insert_stmt.excluded.tagline,
                    "tagline_ru": insert_stmt.excluded.tagline_ru,
                    "description": insert_stmt.excluded.description,
                    "description_ru": insert_stmt.excluded.description_ru,
                    "best_for": insert_stmt.excluded.best_for,
                    "best_for_ru": insert_stmt.excluded.best_for_ru,
                    "speed": insert_stmt.excluded.speed,
                    "tier_required": insert_stmt.excluded.tier_required,
                    "badges": insert_stmt.excluded.badges,
                    "is_active": insert_stmt.excluded.is_active,
                    "sort_index": insert_stmt.excluded.sort_index,
                    "updated_at": sa.func.now(),
                },
            )
        )

    for quality, (description, description_ru) in QUALITY_DESCRIPTIONS.items():
        op.execute(
            sa.update(image_quality_pricing)
            .where(image_quality_pricing.c.quality == quality)
            .values(description=description, description_ru=description_ru)
        )


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()

    text_model_catalog = sa.Table(
        "text_model_catalog",
        metadata,
        sa.Column("provider", sa.String()),
        sa.Column("model_name", sa.String()),
    )
    image_model_catalog = sa.Table(
        "image_model_catalog",
        metadata,
        sa.Column("provider", sa.String()),
        sa.Column("model_name", sa.String()),
    )

    text_names = [row["model_name"] for row in TEXT_MODELS]
    image_names = [row["model_name"] for row in IMAGE_MODELS]

    op.execute(
        sa.delete(text_model_catalog).where(
            sa.and_(
                text_model_catalog.c.provider == "OpenAI",
                text_model_catalog.c.model_name.in_(text_names),
            )
        )
    )
    op.execute(
        sa.delete(image_model_catalog).where(
            sa.and_(
                image_model_catalog.c.provider == "OpenAI",
                image_model_catalog.c.model_name.in_(image_names),
            )
        )
    )
