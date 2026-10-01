"""Publish Q01-Q03 only after frontend production acceptance. Hold this PR until then."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b61"
down_revision = "xw0e1f2a3b60"
branch_labels = None
depends_on = None

ITEM_ID = "2026-09-30-chat-quick-wins"
TITLE_EN = "Safer regeneration and mobile chat tools"
TITLE_RU = "Сохранение чатов и предупреждение перед перегенерацией"
BODY_EN = (
    "Before regenerating an older answer, you can see how many later messages "
    "will be removed and which model will create the new answer. Cancel to keep the chat.\n\n"
    "On mobile, open Chat actions to export your conversation as Markdown or copy "
    "its private web link. The link opens only when you sign in to the same account.\n\n"
    "Settings and your AI allowance now show the same account tier name. "
    "A private tier keeps its own name; the Start multiplier describes its AI capacity."
)
BODY_RU = (
    "Перед перегенерацией старого ответа вы увидите, сколько сообщений после него "
    "будет удалено и какая модель создаст новый ответ. Нажмите «Отмена», чтобы сохранить чат.\n\n"
    "На телефоне откройте меню действий с чатом, чтобы скачать переписку в Markdown "
    "или скопировать ссылку на веб-версию. По ссылке чат доступен только в том же аккаунте.\n\n"
    "В настройках и в разделе лимита ИИ теперь указано одно название тарифа. "
    "Закрытый тариф сохраняет своё название, а множитель Start показывает объём лимита ИИ."
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
