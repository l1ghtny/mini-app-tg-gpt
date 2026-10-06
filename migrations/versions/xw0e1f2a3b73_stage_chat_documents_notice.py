"""Stage chat documents privately until production download acceptance passes."""
from datetime import UTC, datetime
from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b73"
down_revision = "xw0e1f2a3b72"
branch_labels = None
depends_on = None

ITEM_ID = "2026-10-06-chat-documents"
TITLE_EN = "Documents you can download"
TITLE_RU = "Документы, которые можно скачать"
BODY_EN = (
    "Ask for a DOCX or PDF in a chat with Auto tools. The file appears under "
    "the answer; request changes in the same chat to get a new version. Each file "
    "is available for five days, with its download deadline shown beside it. "
    "Refunds that the bank is still processing now show as pending."
)
BODY_RU = (
    "В чате с инструментами «Авто» попросите создать DOCX или PDF. Файл "
    "появится под ответом. Чтобы получить новую версию, попросите внести изменения "
    "в том же чате. Файлы хранятся пять дней; рядом с каждым указан срок скачивания. "
    "Если банк ещё обрабатывает возврат, приложение покажет, что он в процессе."
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
            'file-text', '[]'::jsonb, '2.5.0', false, :now, false, :now, :now
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
