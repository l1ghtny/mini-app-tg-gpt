"""Announce production-verified document download improvements."""
from datetime import UTC, datetime
from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b74"
down_revision = "xw0e1f2a3b73"
branch_labels = None
depends_on = None

ITEM_ID = "2026-10-08-document-downloads"
TITLE_EN = "Easier file downloads"
TITLE_RU = "Скачивать файлы стало удобнее"
BODY_EN = (
    "Tap a DOCX or PDF under an answer to download it. Supported Telegram apps "
    "now show a download dialog without opening an empty browser window. The chat "
    "shows when the download starts or is cancelled. If the dialog is unavailable, "
    "choose Download in browser. Browser downloads also stay in the current tab."
)
BODY_RU = (
    "Нажмите на DOCX или PDF под ответом, чтобы скачать файл. В поддерживаемых "
    "версиях Telegram откроется окно скачивания, без пустой страницы в браузере. "
    "В чате видно, началось ли скачивание или вы его отменили. Если окно недоступно, "
    "нажмите «Скачать через браузер». При скачивании в браузере новая вкладка тоже "
    "не открывается."
)

def upgrade():
    now = datetime.now(UTC).replace(tzinfo=None)
    op.execute(sa.text("""
        INSERT INTO whats_new_item (
            id, kind, title_en, title_ru, body_en, body_ru,
            icon, audience_plans, min_app_version, pinned,
            published_at, is_active, created_at, updated_at
        ) VALUES (
            :id, 'feature', :title_en, :title_ru, :body_en, :body_ru,
            'file-text', '[]'::jsonb, '2.5.1', false, :now, true, :now, :now
        ) ON CONFLICT (id) DO NOTHING
    """).bindparams(
        sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
        sa.bindparam("title_en", value=TITLE_EN, type_=sa.String()),
        sa.bindparam("title_ru", value=TITLE_RU, type_=sa.String()),
        sa.bindparam("body_en", value=BODY_EN, type_=sa.Text()),
        sa.bindparam("body_ru", value=BODY_RU, type_=sa.Text()),
        sa.bindparam("now", value=now, type_=sa.DateTime()),
    ))

def downgrade():
    op.execute(sa.text("DELETE FROM whats_new_item WHERE id = :id").bindparams(
        sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
    ))
