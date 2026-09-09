"""announce localized error notifications

Revision ID: xp3d4e5f6a7b
Revises: xo2c3d4e5f6a
Create Date: 2026-09-09
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "xp3d4e5f6a7b"
down_revision: Union[str, Sequence[str], None] = "xo2c3d4e5f6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ITEM_ID = "2026-09-09-localized-error-notifications"
PUBLISHED_AT = datetime(2026, 9, 9, 2, 5, 43)
TITLE_EN = "Error messages in your language"
TITLE_RU = "Сообщения об ошибках на вашем языке"
BODY_EN = (
    "Error notifications now follow your language setting, including upload, "
    "payment, account, and chat failures. You will see readable messages instead "
    "of untranslated technical errors."
)
BODY_RU = (
    "Уведомления об ошибках теперь показываются на выбранном языке: при загрузке "
    "файлов, оплате, работе с аккаунтом и в чате. Вместо непереведённых технических "
    "ошибок вы увидите понятные сообщения."
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
