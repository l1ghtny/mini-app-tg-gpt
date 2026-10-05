"""Announce production-verified file-library and account recovery improvements."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b66"
down_revision = "xw0e1f2a3b65"
branch_labels = None
depends_on = None

ITEM_ID = '2026-10-05-file-library-retention'
TITLE_EN = 'Files are easier to find and manage'
TITLE_RU = 'Файлы проще находить и хранить'
BODY_EN = 'In Settings → Documents, search by file name, filter and sort files, and see which chats and projects use them. New uploads keep their original names.\n\nAttaching a temporary file or successfully searching it renews its storage period. Pinned files stay until unpinned or deleted. Existing files whose storage period had ended have received a full grace period.\n\nAccount settings distinguish loading failures from missing passkeys or payment methods and let you retry.'
BODY_RU = 'В разделе «Настройки → Документы» можно искать файлы по имени, фильтровать и сортировать список и смотреть, к каким чатам и проектам прикреплён файл. Новые файлы сохраняют исходные имена.\n\nПосле прикрепления или успешного поиска по временному файлу его срок хранения начинается заново. Закреплённые файлы хранятся, пока вы не открепите или не удалите их. Старые файлы с истёкшим сроком получили полный дополнительный период хранения.\n\nЕсли ключи доступа или способы оплаты не удалось загрузить, настройки показывают ошибку и позволяют повторить загрузку.'

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
