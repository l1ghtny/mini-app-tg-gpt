"""Announce production-verified answer usage and focused settings."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b70"
down_revision = "xw0e1f2a3b69"
branch_labels = None
depends_on = None

ITEM_ID = "2026-10-06-answer-usage-settings"
TITLE_EN = "See usage for each answer"
TITLE_RU = "Расход на каждый ответ"
BODY_EN = (
    "Open Usage beside an answer to see how much of your allowance it used. "
    "Luna replies now show their own remaining allowance, with an action to start "
    "a Luna chat without tools. Mobile settings are grouped into focused pages, "
    "with protection for unsaved AI preferences."
)
BODY_RU = (
    "Нажмите «Расход» рядом с ответом, чтобы увидеть списание из своего лимита. "
    "Для ответов Luna отдельно показан остаток лимита и есть кнопка нового чата "
    "без инструментов. Настройки на телефоне разделены на страницы; несохранённые "
    "предпочтения ИИ защищены от случайного закрытия."
)


def upgrade():
    now = datetime.now(UTC).replace(tzinfo=None)
    op.execute(
        sa.text("""
        INSERT INTO whats_new_item (
            id, kind, title_en, title_ru, body_en, body_ru,
            icon, image_url, cta_label_en, cta_label_ru, cta_kind, cta_value,
            audience_plans, min_app_version, pinned, starts_at, expires_at,
            published_at, is_active, created_at, updated_at
        ) VALUES (
            :id, 'improvement', :title_en, :title_ru, :body_en, :body_ru,
            'wrench', NULL, NULL, NULL, NULL, NULL,
            '[]'::jsonb, NULL, false, NULL, NULL, :now, true, :now, :now
        ) ON CONFLICT (id) DO NOTHING
        """).bindparams(
            sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
            sa.bindparam("title_en", value=TITLE_EN, type_=sa.String()),
            sa.bindparam("title_ru", value=TITLE_RU, type_=sa.String()),
            sa.bindparam("body_en", value=BODY_EN, type_=sa.Text()),
            sa.bindparam("body_ru", value=BODY_RU, type_=sa.Text()),
            sa.bindparam("now", value=now, type_=sa.DateTime()),
        )
    )


def downgrade():
    op.execute(
        sa.text("DELETE FROM whats_new_item WHERE id = :id").bindparams(
            sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
        )
    )
