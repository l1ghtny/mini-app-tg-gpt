"""Publish document selection repair after production acceptance."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b50"
down_revision = "xw0e1f2a3b4f"
branch_labels = None
depends_on = None

ITEM_ID = "2026-09-24-document-selection"
TITLE_EN = "Documents stay attached and ready to use"
TITLE_RU = "Прикреплённые документы доступны в чате"
BODY_EN = (
    "Select uploaded files from the paperclip menu to use them in your chat. "
    "The attachment count now updates as soon as your selection is saved, "
    "and you can remove files from the chat on the first try. "
    "Attaching a file enables document search; Auto stays on if already selected."
)
BODY_RU = (
    "Выберите загруженные файлы в меню со скрепкой, чтобы работать с ними в чате. "
    "Счётчик вложений теперь обновляется сразу после сохранения выбора, "
    "а убрать файл из чата можно с первой попытки. "
    "Прикрепление файла включает поиск по документам. Если выбран режим «Авто», он сохраняется."
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
