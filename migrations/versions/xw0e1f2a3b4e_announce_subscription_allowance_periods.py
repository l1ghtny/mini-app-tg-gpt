"""Publish subscription-period release notes after live acceptance."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b4e"
down_revision = "xw0e1f2a3b4d"
branch_labels = None
depends_on = None

ITEM_ID = "2026-09-22-subscription-allowance-periods"
TITLE_EN = "Allowance dates follow your subscription"
TITLE_RU = "Даты обновления лимита привязаны к подписке"
BODY_EN = (
    "Your allowance now follows your subscription period. A 30-day invitation "
    "includes one allowance for those 30 days. In Subscription, you can see "
    "when your allowance resets or your access ends."
)
BODY_RU = (
    "Лимит теперь действует в течение периода подписки. Приглашение на 30 дней "
    "даёт один лимит на весь этот срок. В разделе «Подписка» указано, когда "
    "лимит обновится или закончится доступ."
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
