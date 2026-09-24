"""Extend the document notice after indexing feedback reaches production."""

from importlib import import_module

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b51"
down_revision = "xw0e1f2a3b50"
branch_labels = None
depends_on = None

previous = import_module("migrations.versions.xw0e1f2a3b50_announce_document_selection")
ITEM_ID = previous.ITEM_ID
BODY_EN = previous.BODY_EN + (
    " While files are uploading or indexing, the attachment counter shows progress "
    "and Send stays disabled. You can keep writing; Send becomes available when indexing finishes."
)
BODY_RU = previous.BODY_RU + (
    " Пока файлы загружаются или обрабатываются, у счётчика вложений виден индикатор, "
    "а отправка сообщения недоступна. Вы можете продолжать писать: "
    "кнопка отправки станет доступна, когда обработка завершится."
)


def _update(body_en, body_ru):
    # Preserve the existing publication time and read state: this is one release notice.
    op.execute(
        sa.text("""
        UPDATE whats_new_item
        SET body_en = :body_en, body_ru = :body_ru, updated_at = CURRENT_TIMESTAMP
        WHERE id = :id
        """).bindparams(
            sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
            sa.bindparam("body_en", value=body_en, type_=sa.Text()),
            sa.bindparam("body_ru", value=body_ru, type_=sa.Text()),
        )
    )


def upgrade():
    _update(BODY_EN, BODY_RU)


def downgrade():
    _update(previous.BODY_EN, previous.BODY_RU)
