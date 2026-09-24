"""Publish startup loading improvement after production acceptance."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b53"
down_revision = "xw0e1f2a3b52"
branch_labels = None
depends_on = None

ITEM_ID = "2026-09-24-startup-loading"
TITLE_EN = "Less waiting when opening the app"
TITLE_RU = "Меньше ожидания при запуске"
BODY_EN = (
    "Reduced unnecessary waiting for your settings and model list when opening "
    "Lightny AI, including on slower connections. No settings changes are needed."
)
BODY_RU = (
    "Убрали лишние задержки при загрузке настроек и списка моделей в Lightny AI, "
    "в том числе при медленном соединении. Ничего настраивать не нужно."
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
