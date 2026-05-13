"""add whats new entries for folders and wizard

Revision ID: e6f5d4c3b2a1
Revises: d4c3b2a1908f
Create Date: 2026-05-08 22:05:00.000000
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6f5d4c3b2a1"
down_revision: Union[str, Sequence[str], None] = "d4c3b2a1908f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ITEM_FOLDER_REWORK_ID = "2026-05-08-folder-rework-mobile-sidebar"
ITEM_WIZARD_ID = "2026-05-08-ai-personalization-wizard"


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "whats_new_item" not in inspector.get_table_names():
        return

    now = _now_naive()
    one_hour_ago = now - timedelta(hours=1)

    bind.execute(
        sa.text(
            """
            INSERT INTO whats_new_item (
                id, kind, title_en, title_ru, body_en, body_ru,
                icon, cta_label_en, cta_label_ru, cta_kind, cta_value,
                audience_plans, pinned, starts_at, expires_at,
                published_at, is_active, created_at, updated_at
            ) VALUES (
                :id, :kind, :title_en, :title_ru, :body_en, :body_ru,
                :icon, :cta_label_en, :cta_label_ru, :cta_kind, :cta_value,
                '[]'::jsonb, false, NULL, NULL,
                :published_at, true, :now, :now
            )
            ON CONFLICT (id) DO UPDATE SET
                kind = EXCLUDED.kind,
                title_en = EXCLUDED.title_en,
                title_ru = EXCLUDED.title_ru,
                body_en = EXCLUDED.body_en,
                body_ru = EXCLUDED.body_ru,
                icon = EXCLUDED.icon,
                cta_label_en = EXCLUDED.cta_label_en,
                cta_label_ru = EXCLUDED.cta_label_ru,
                cta_kind = EXCLUDED.cta_kind,
                cta_value = EXCLUDED.cta_value,
                audience_plans = EXCLUDED.audience_plans,
                pinned = EXCLUDED.pinned,
                starts_at = EXCLUDED.starts_at,
                expires_at = EXCLUDED.expires_at,
                published_at = EXCLUDED.published_at,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "id": ITEM_FOLDER_REWORK_ID,
            "kind": "improvement",
            "title_en": "Folders reworked on mobile",
            "title_ru": "Папки переработаны на мобильных",
            "body_en": (
                "Mobile sidebar navigation for folders has been redesigned: "
                "you can browse folders as a dedicated view, filter chat list by folder context, "
                "and start a new chat directly inside the selected folder."
            ),
            "body_ru": (
                "Мобильная навигация по папкам в сайдбаре переработана: "
                "появился отдельный режим просмотра папок, фильтрация списка чатов по выбранной папке "
                "и создание нового чата сразу внутри выбранной папки."
            ),
            "icon": "folder",
            "cta_label_en": "Open settings",
            "cta_label_ru": "Открыть настройки",
            "cta_kind": "open_settings",
            "cta_value": None,
            "published_at": one_hour_ago,
            "now": now,
        },
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO whats_new_item (
                id, kind, title_en, title_ru, body_en, body_ru,
                icon, cta_label_en, cta_label_ru, cta_kind, cta_value,
                audience_plans, pinned, starts_at, expires_at,
                published_at, is_active, created_at, updated_at
            ) VALUES (
                :id, :kind, :title_en, :title_ru, :body_en, :body_ru,
                :icon, :cta_label_en, :cta_label_ru, :cta_kind, :cta_value,
                '[]'::jsonb, false, NULL, NULL,
                :published_at, true, :now, :now
            )
            ON CONFLICT (id) DO UPDATE SET
                kind = EXCLUDED.kind,
                title_en = EXCLUDED.title_en,
                title_ru = EXCLUDED.title_ru,
                body_en = EXCLUDED.body_en,
                body_ru = EXCLUDED.body_ru,
                icon = EXCLUDED.icon,
                cta_label_en = EXCLUDED.cta_label_en,
                cta_label_ru = EXCLUDED.cta_label_ru,
                cta_kind = EXCLUDED.cta_kind,
                cta_value = EXCLUDED.cta_value,
                audience_plans = EXCLUDED.audience_plans,
                pinned = EXCLUDED.pinned,
                starts_at = EXCLUDED.starts_at,
                expires_at = EXCLUDED.expires_at,
                published_at = EXCLUDED.published_at,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "id": ITEM_WIZARD_ID,
            "kind": "feature",
            "title_en": "New AI personalization wizard",
            "title_ru": "Новый мастер персонализации AI",
            "body_en": (
                "You can now customize how AI responds using a setup wizard in Settings. "
                "It builds your main user prompt from your choices, then lets you review and edit it before saving."
            ),
            "body_ru": (
                "Теперь можно настроить стиль ответов AI через мастер в Настройках. "
                "Он собирает основной пользовательский промпт из ваших ответов, после чего его можно просмотреть и отредактировать перед сохранением."
            ),
            "icon": "sparkles",
            "cta_label_en": "Open settings",
            "cta_label_ru": "Открыть настройки",
            "cta_kind": "open_settings",
            "cta_value": None,
            "published_at": now,
            "now": now,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "whats_new_item" not in inspector.get_table_names():
        return

    bind.execute(
        sa.text(
            """
            DELETE FROM whats_new_item
            WHERE id IN (:folder_id, :wizard_id)
            """
        ),
        {
            "folder_id": ITEM_FOLDER_REWORK_ID,
            "wizard_id": ITEM_WIZARD_ID,
        },
    )
