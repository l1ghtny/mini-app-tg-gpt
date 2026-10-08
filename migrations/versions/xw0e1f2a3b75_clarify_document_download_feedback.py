"""Clarify the existing download notice after the production UX follow-up."""
from importlib import import_module

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b75"
down_revision = "xw0e1f2a3b74"
branch_labels = None
depends_on = None

ITEM_ID = "2026-10-08-document-downloads"
BODY_EN = (
    "Use the DOCX or PDF button under an answer to download the file. Redundant "
    "private file links no longer appear in the answer. Browser downloads stay in "
    "the current tab; check your browser's downloads for progress and completion. "
    "Supported Telegram apps show a download dialog and cancellation feedback. "
    "If the dialog is unavailable, choose Download in browser."
)
BODY_RU = (
    "Чтобы скачать DOCX или PDF, нажмите на файл под ответом. Лишние ссылки на "
    "этот файл в тексте ответа больше не отображаются. В браузере скачивание "
    "идёт без новой вкладки; ход загрузки и её завершение можно проверить в "
    "списке загрузок браузера. В поддерживаемых версиях Telegram открывается "
    "окно скачивания, а при отмене появляется сообщение в чате. Если окно "
    "недоступно, нажмите «Скачать через браузер»."
)


def update_notice(body_en, body_ru, version):
    op.execute(sa.text("""
        UPDATE whats_new_item SET body_en = :body_en, body_ru = :body_ru,
            min_app_version = :version, updated_at = timezone('utc', now())
        WHERE id = :id
    """).bindparams(
        sa.bindparam("body_en", value=body_en, type_=sa.Text()),
        sa.bindparam("body_ru", value=body_ru, type_=sa.Text()),
        sa.bindparam("version", value=version, type_=sa.String()),
        sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
    ))


def upgrade():
    update_notice(BODY_EN, BODY_RU, "2.5.2")


def downgrade():
    previous = import_module("migrations.versions.xw0e1f2a3b74_announce_document_downloads")
    update_notice(previous.BODY_EN, previous.BODY_RU, "2.5.1")
