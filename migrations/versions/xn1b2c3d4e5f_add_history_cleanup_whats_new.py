"""announce selective chat and project cleanup

Revision ID: xn1b2c3d4e5f
Revises: xm0a1b2c3d4e
Create Date: 2026-09-08
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "xn1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "xm0a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ITEM_ID = "2026-09-08-selective-history-cleanup"
PUBLISHED_AT = datetime(2026, 9, 8, 12, 45, 54)
TITLE_EN = "Clean up several chats at once"
TITLE_RU = "Удаляйте ненужные чаты за один раз"
BODY_EN = (
    "Open the menu next to Chats and choose Manage chats & projects. Select "
    "individual chats or entire projects, review the selection, then delete them "
    "together. The shortcut for chats outside projects skips favorites by default. "
    "Files in your document library stay available."
)
BODY_RU = (
    "Откройте меню рядом с заголовком «Диалоги» и выберите «Управление чатами и "
    "проектами». Отметьте отдельные чаты или целые проекты, проверьте список и "
    "удалите всё выбранное за один раз. При выборе всех чатов вне проектов "
    "избранные по умолчанию остаются. Файлы в библиотеке документов сохранятся."
)


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def upgrade() -> None:
    now = _now_naive()
    statement = sa.text(
        """
            INSERT INTO whats_new_item (
                id, kind, title_en, title_ru, body_en, body_ru,
                icon, image_url, cta_label_en, cta_label_ru, cta_kind, cta_value,
                audience_plans, min_app_version, pinned, starts_at, expires_at,
                published_at, is_active, created_at, updated_at
            ) VALUES (
                :id, 'improvement', :title_en, :title_ru, :body_en, :body_ru,
                'wrench', NULL, NULL, NULL, NULL, NULL,
                '[]'::jsonb, NULL, false, NULL, NULL,
                :published_at, true, :now, :now
            )
            ON CONFLICT (id) DO UPDATE SET
                kind = EXCLUDED.kind,
                title_en = EXCLUDED.title_en,
                title_ru = EXCLUDED.title_ru,
                body_en = EXCLUDED.body_en,
                body_ru = EXCLUDED.body_ru,
                icon = EXCLUDED.icon,
                image_url = EXCLUDED.image_url,
                cta_label_en = EXCLUDED.cta_label_en,
                cta_label_ru = EXCLUDED.cta_label_ru,
                cta_kind = EXCLUDED.cta_kind,
                cta_value = EXCLUDED.cta_value,
                audience_plans = EXCLUDED.audience_plans,
                min_app_version = EXCLUDED.min_app_version,
                pinned = EXCLUDED.pinned,
                starts_at = EXCLUDED.starts_at,
                expires_at = EXCLUDED.expires_at,
                published_at = EXCLUDED.published_at,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at
            """
    ).bindparams(
        sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
        sa.bindparam("title_en", value=TITLE_EN, type_=sa.String()),
        sa.bindparam("title_ru", value=TITLE_RU, type_=sa.String()),
        sa.bindparam("body_en", value=BODY_EN, type_=sa.Text()),
        sa.bindparam("body_ru", value=BODY_RU, type_=sa.Text()),
        sa.bindparam("published_at", value=PUBLISHED_AT, type_=sa.DateTime()),
        sa.bindparam("now", value=now, type_=sa.DateTime()),
    )
    op.execute(statement)


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM whats_new_item WHERE id = :id").bindparams(
            sa.bindparam("id", value=ITEM_ID, type_=sa.String())
        )
    )
