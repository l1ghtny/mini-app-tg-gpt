"""seed_google_models_and_limits

Revision ID: ed3f5ae64826
Revises: d9e8f7a6b5c4
Create Date: 2026-05-23 15:44:52.253056

"""
from typing import Sequence, Union
import uuid
from decimal import Decimal

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = 'ed3f5ae64826'
down_revision: Union[str, Sequence[str], None] = 'd9e8f7a6b5c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("34e7db20-59d0-4ee6-8189-83e2d12f4685")


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


TEXT_MODELS = [
    {
        "provider": "google",
        "model_name": "gemini-3.1-flash-lite",
        "display_name": "Gemini 3.1 Flash Lite",
        "display_name_ru": "Gemini 3.1 Flash Lite",
        "tagline": "Ultra-fast, light everyday answers",
        "tagline_ru": "Сверхбыстрые, легкие ответы на каждый день",
        "description": "Best for short questions, quick drafting, and translation tasks.",
        "description_ru": "Лучше всего для коротких вопросов, быстрых черновиков и задач перевода.",
        "best_for": ["Quick answers", "Summaries", "Translations"],
        "best_for_ru": ["Быстрые ответы", "Саммари", "Переводы"],
        "not_great_for": ["Complex coding", "Advanced reasoning"],
        "not_great_for_ru": ["Сложный код", "Глубокие рассуждения"],
        "speed": "fast",
        "intelligence": 2,
        "context_window": 1000000,
        "supports": {
            "vision": True,
            "web_search": True,
            "file_search": False,
            "image_gen": False,
            "reasoning": False,
            "thinking": False
        },
        "tier_required": {"slug": "free", "min_rank": 0},
        "badges": [],
        "credit_cost_hint": Decimal("1.0"),
        "sort_index": 50,
    },
    {
        "provider": "google",
        "model_name": "gemini-3.5-flash",
        "display_name": "Gemini 3.5 Flash",
        "display_name_ru": "Gemini 3.5 Flash",
        "tagline": "Smart, fast and capable",
        "tagline_ru": "Умный, быстрый и способный",
        "description": "Great default choice for most coding, analysis, and multimodal queries.",
        "description_ru": "Отличный базовый выбор для кода, анализа и мультимодальных запросов.",
        "best_for": ["Coding", "Analysis", "Thinking mode"],
        "best_for_ru": ["Программирование", "Анализ", "Режим рассуждения"],
        "not_great_for": ["Ultra-deep logic tasks"],
        "not_great_for_ru": ["Задачи с глубочайшей логикой"],
        "speed": "fast",
        "intelligence": 4,
        "context_window": 1000000,
        "supports": {
            "vision": True,
            "web_search": True,
            "file_search": False,
            "image_gen": False,
            "reasoning": True,
            "thinking": True
        },
        "tier_required": {"slug": "basic", "min_rank": 1},
        "badges": ["recommended"],
        "credit_cost_hint": Decimal("1.5"),
        "sort_index": 60,
    },
    {
        "provider": "google",
        "model_name": "gemini-3.1-pro-preview",
        "display_name": "Gemini 3.1 Pro",
        "display_name_ru": "Gemini 3.1 Pro",
        "tagline": "Peak intelligence for complex tasks",
        "tagline_ru": "Пиковый интеллект для сложных задач",
        "description": "Best for complex programming, reasoning, and large-scale agentic workflows.",
        "description_ru": "Лучше всего для сложного программирования, рассуждений и масштабных задач.",
        "best_for": ["Hard reasoning", "Advanced coding", "Large context tasks"],
        "best_for_ru": ["Сложные рассуждения", "Продвинутый код", "Задачи с большим контекстом"],
        "not_great_for": ["Budget-sensitive high volume tasks"],
        "not_great_for_ru": ["Массовые простые задачи"],
        "speed": "medium",
        "intelligence": 5,
        "context_window": 1000000,
        "supports": {
            "vision": True,
            "web_search": True,
            "file_search": False,
            "image_gen": False,
            "reasoning": True,
            "thinking": True
        },
        "tier_required": {"slug": "premium", "min_rank": 3},
        "badges": ["pro"],
        "credit_cost_hint": Decimal("5.0"),
        "sort_index": 70,
    }
]


IMAGE_MODELS = [
    {
        "provider": "google",
        "model_name": "gemini-2.5-flash-image",
        "display_name": "Nano Banana",
        "display_name_ru": "Nano Banana",
        "tagline": "Fast and cost-effective image generation",
        "tagline_ru": "Быстрая и экономичная генерация картинок",
        "description": "Best for fast previews and basic conceptual artwork.",
        "description_ru": "Лучше всего для быстрых эскизов и простых визуалов.",
        "best_for": ["Fast previews", "Basic compositions"],
        "best_for_ru": ["Быстрый предпросмотр", "Простые композиции"],
        "speed": "fast",
        "tier_required": {"slug": "free", "min_rank": 0},
        "badges": [],
        "sort_index": 50,
    },
    {
        "provider": "google",
        "model_name": "gemini-3.1-flash-image-preview",
        "display_name": "Nano Banana 2",
        "display_name_ru": "Nano Banana 2",
        "tagline": "Balanced details and prompt adherence",
        "tagline_ru": "Сбалансированные детали и следование промпту",
        "description": "Great for illustrative assets and everyday creatives.",
        "description_ru": "Отлично подходит для иллюстраций и креативов на каждый день.",
        "best_for": ["Illustrations", "Everyday design"],
        "best_for_ru": ["Иллюстрации", "Дизайн на каждый день"],
        "speed": "medium",
        "tier_required": {"slug": "basic", "min_rank": 1},
        "badges": ["recommended", "pro"],
        "sort_index": 60,
    },
    {
        "provider": "google",
        "model_name": "gemini-3-pro-image-preview",
        "display_name": "Nano Banana Pro",
        "display_name_ru": "Nano Banana Pro",
        "tagline": "High-fidelity artistic generation",
        "tagline_ru": "Высокодетализированная художественная генерация",
        "description": "Best for professional layouts, photorealism, and polished final assets.",
        "description_ru": "Лучше всего для профессиональных макетов, фотореализма и готовых материалов.",
        "best_for": ["Photorealism", "High-detail art", "Professional assets"],
        "best_for_ru": ["Фотореализм", "Детализированный арт", "Профессиональные материалы"],
        "speed": "slow",
        "tier_required": {"slug": "premium", "min_rank": 3},
        "badges": ["pro"],
        "sort_index": 70,
    }
]


IMAGE_QUALITY_PRICING = [
    # gemini-2.5-flash-image
    {"image_model": "gemini-2.5-flash-image", "quality": "low", "credit_cost": 1.0, "description": "Fast preview", "description_ru": "Быстрый предпросмотр"},
    # gemini-3.1-flash-image-preview
    {"image_model": "gemini-3.1-flash-image-preview", "quality": "low", "credit_cost": 1.0, "description": "Fast preview", "description_ru": "Быстрый предпросмотр"},
    {"image_model": "gemini-3.1-flash-image-preview", "quality": "medium", "credit_cost": 3.0, "description": "Balanced quality", "description_ru": "Сбалансированное качество"},
    # gemini-3-pro-image-preview
    {"image_model": "gemini-3-pro-image-preview", "quality": "low", "credit_cost": 1.0, "description": "Fast preview", "description_ru": "Быстрый предпросмотр"},
    {"image_model": "gemini-3-pro-image-preview", "quality": "medium", "credit_cost": 3.0, "description": "Balanced quality", "description_ru": "Сбалансированное качество"},
    {"image_model": "gemini-3-pro-image-preview", "quality": "high", "credit_cost": 9.0, "description": "Crisp final quality", "description_ru": "Максимальная детализация"},
]


def upgrade() -> None:
    # 1. Add default model columns to app_user table
    op.add_column("app_user", sa.Column("default_text_model", sa.String(), nullable=False, server_default="gpt-5.4-nano"))
    op.add_column("app_user", sa.Column("default_image_model", sa.String(), nullable=False, server_default="gpt-image-1.5"))

    # 2. Setup metadata and table reflections for catalog tables
    bind = op.get_bind()
    metadata = sa.MetaData()

    text_model_catalog = sa.Table(
        "text_model_catalog",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
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
    )

    image_model_catalog = sa.Table(
        "image_model_catalog",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
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
    )

    image_quality_pricing = sa.Table(
        "image_quality_pricing",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("image_model", sa.String()),
        sa.Column("quality", sa.String()),
        sa.Column("credit_cost", sa.Float()),
        sa.Column("description", sa.String()),
        sa.Column("description_ru", sa.String()),
        sa.Column("is_active", sa.Boolean()),
    )

    # 3. Seed text models catalog
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
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[text_model_catalog.c.id],
                set_={
                    "provider": insert_stmt.excluded.provider,
                    "model_name": insert_stmt.excluded.model_name,
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
                }
            )
        )

    # 4. Seed image models catalog
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
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[image_model_catalog.c.id],
                set_={
                    "provider": insert_stmt.excluded.provider,
                    "model_name": insert_stmt.excluded.model_name,
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
                }
            )
        )

    # 5. Seed image quality pricing
    for row in IMAGE_QUALITY_PRICING:
        seed_row = {
            "id": _stable_uuid(f"image-quality-pricing:{row['image_model']}:{row['quality']}"),
            "image_model": row["image_model"],
            "quality": row["quality"],
            "credit_cost": row["credit_cost"],
            "description": row["description"],
            "description_ru": row["description_ru"],
            "is_active": True,
        }
        insert_stmt = pg_insert(image_quality_pricing).values(**seed_row)
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[image_quality_pricing.c.id],
                set_={
                    "image_model": insert_stmt.excluded.image_model,
                    "quality": insert_stmt.excluded.quality,
                    "credit_cost": insert_stmt.excluded.credit_cost,
                    "description": insert_stmt.excluded.description,
                    "description_ru": insert_stmt.excluded.description_ru,
                    "is_active": insert_stmt.excluded.is_active,
                }
            )
        )

    # 6. Mirror subscription limits for Google models
    tier_model_limit = sa.Table(
        "tier_model_limit",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
    )

    tier_image_model_limit = sa.Table(
        "tier_image_model_limit",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("image_model", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
    )

    # Query all current tier model limits to fetch existing configurations
    model_limits = bind.execute(sa.text("SELECT tier_id, model_name, monthly_requests FROM tier_model_limit")).fetchall()
    image_limits = bind.execute(sa.text("SELECT tier_id, image_model, monthly_requests FROM tier_image_model_limit")).fetchall()

    # Build lookup map of (tier_id, model_name) -> requests
    text_lim_map = {}
    for r in model_limits:
        text_lim_map[(r[0], r[1])] = r[2]

    image_lim_map = {}
    for r in image_limits:
        image_lim_map[(r[0], r[1])] = r[2]

    # Unique list of tier_ids
    tier_ids = {r[0] for r in model_limits}.union({r[0] for r in image_limits})

    # Text model mirroring specifications
    text_spec = {
        "gemini-3.1-flash-lite": "gpt-5.4-nano",
        "gemini-3.5-flash": "gpt-5.4-mini",
        "gemini-3.1-pro-preview": "gpt-5.5"
    }

    # Image model mirroring specifications
    image_spec = {
        "gemini-2.5-flash-image": "gpt-image-1.5",
        "gemini-3.1-flash-image-preview": "gpt-image-1.5",
        "gemini-3-pro-image-preview": "gpt-image-2"
    }

    for tier_id in tier_ids:
        # Mirror Text model limits
        for target_model, source_model in text_spec.items():
            limit = text_lim_map.get((tier_id, source_model))
            if limit is not None:
                seed_row = {
                    "id": _stable_uuid(f"tier-limit:{str(tier_id)}:{target_model}"),
                    "tier_id": tier_id,
                    "model_name": target_model,
                    "monthly_requests": limit,
                }
                insert_stmt = pg_insert(tier_model_limit).values(**seed_row)
                bind.execute(
                    insert_stmt.on_conflict_do_update(
                        index_elements=[tier_model_limit.c.id],
                        set_={"monthly_requests": insert_stmt.excluded.monthly_requests}
                    )
                )

        # Mirror Image model limits
        for target_model, source_model in image_spec.items():
            limit = image_lim_map.get((tier_id, source_model))
            if limit is not None:
                seed_row = {
                    "id": _stable_uuid(f"tier-image-limit:{str(tier_id)}:{target_model}"),
                    "tier_id": tier_id,
                    "image_model": target_model,
                    "monthly_requests": limit,
                }
                insert_stmt = pg_insert(tier_image_model_limit).values(**seed_row)
                bind.execute(
                    insert_stmt.on_conflict_do_update(
                        index_elements=[tier_image_model_limit.c.id],
                        set_={"monthly_requests": insert_stmt.excluded.monthly_requests}
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()

    # Delete limits first
    op.execute(sa.text("DELETE FROM tier_model_limit WHERE model_name IN ('gemini-3.1-flash-lite', 'gemini-3.5-flash', 'gemini-3.1-pro-preview')"))
    op.execute(sa.text("DELETE FROM tier_image_model_limit WHERE image_model IN ('gemini-2.5-flash-image', 'gemini-3.1-flash-image-preview', 'gemini-3-pro-image-preview')"))

    # Delete catalog items
    op.execute(sa.text("DELETE FROM text_model_catalog WHERE provider = 'google'"))
    op.execute(sa.text("DELETE FROM image_model_catalog WHERE provider = 'google'"))
    op.execute(sa.text("DELETE FROM image_quality_pricing WHERE image_model IN ('gemini-2.5-flash-image', 'gemini-3.1-flash-image-preview', 'gemini-3-pro-image-preview')"))

    # Drop columns from app_user
    op.drop_column("app_user", "default_text_model")
    op.drop_column("app_user", "default_image_model")
