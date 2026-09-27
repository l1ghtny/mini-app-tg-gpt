"""Stage the SRT document announcement until production acceptance passes."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b58"
down_revision = "xw0e1f2a3b57"
branch_labels = None
depends_on = None

ITEM_ID = "2026-09-27-srt-documents"
TITLE_EN = "Upload subtitle files as documents"
TITLE_RU = "Загружайте субтитры как документы"
BODY_EN = (
    "You can now upload .srt subtitle files through Add attachment or Documents. "
    "Attach one to a chat to ask about its dialogue and timestamps. "
    "Available on plans that include documents."
)
BODY_RU = (
    "Теперь файлы субтитров .srt можно загрузить через «Добавить вложение» "
    "или раздел «Документы». Прикрепите файл к чату, чтобы спросить о репликах "
    "и времени их появления. Доступно на тарифах с поддержкой документов."
)


def upgrade():
    # Activate after the production backend and frontend accept a real SRT upload.
    now = datetime.now(UTC).replace(tzinfo=None)
    op.execute(
        sa.text("""
        INSERT INTO whats_new_item (
            id, kind, title_en, title_ru, body_en, body_ru,
            icon, image_url, cta_label_en, cta_label_ru, cta_kind, cta_value,
            audience_plans, min_app_version, pinned, starts_at, expires_at,
            published_at, is_active, created_at, updated_at
        ) VALUES (
            :id, 'feature', :title_en, :title_ru, :body_en, :body_ru,
            'sparkles', NULL, NULL, NULL, NULL, NULL,
            '[]'::jsonb, NULL, false, NULL, NULL, :now, false, :now, :now
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
