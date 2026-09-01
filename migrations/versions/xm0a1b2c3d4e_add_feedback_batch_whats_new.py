"""add feedback batch whats new entry

Revision ID: xm0a1b2c3d4e
Revises: xl9f0a1b2c3d
Create Date: 2026-09-01 21:55:00.000000
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "xm0a1b2c3d4e"
down_revision: Union[str, Sequence[str], None] = "xl9f0a1b2c3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ITEM_ID = "2026-09-01-drafts-favorites-chat-recovery"
PUBLISHED_AT = datetime(2026, 9, 1, 19, 46, 23)
TITLE_EN = "Chats are now easier and more reliable"
TITLE_RU = "Чаты стали удобнее и надёжнее"
BODY_EN = (
    "Drafts now save automatically and sync across devices, and important chats "
    "can be added to Favorites. Failed image uploads stay attached, so you can "
    "retry them instead of starting over. If an app update leaves an outdated "
    "page open, the app refreshes it automatically. Image previews are larger, "
    "and the full-screen viewer keeps its Close button clear of Telegram controls."
)
BODY_RU = (
    "Черновики теперь сохраняются автоматически и синхронизируются между "
    "устройствами, а важные чаты можно добавить в Избранное. Если изображение не "
    "загрузилось, оно останется во вложениях — загрузку можно повторить, не начиная "
    "заново. Если после обновления осталась открыта старая версия страницы, "
    "приложение перезагрузит её автоматически. Превью изображений стали крупнее, а "
    "кнопка «Закрыть» в полноэкранном просмотре больше не перекрывается элементами "
    "Telegram."
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
                :id, 'feature', :title_en, :title_ru, :body_en, :body_ru,
                'sparkles', NULL, NULL, NULL, NULL, NULL,
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
