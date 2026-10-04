"""Announce the production-verified no-hold allowance and research policy."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b64"
down_revision = "xw0e1f2a3b63"
branch_labels = None
depends_on = None

ITEM_ID = "2026-10-04-generation-policy"
TITLE_EN = "More room for AI answers"
TITLE_RU = "Больше возможностей для ответов ИИ"
BODY_EN = (
    "Your AI allowance has increased. Ordinary conversations no longer ask you "
    "to confirm a maximum charge before each reply: your allowance is charged for actual usage.\n\n"
    "Research can follow up on earlier search results. Long answers have more room "
    "and can continue once if they reach the response limit and resources are available.\n\n"
    "You can run up to two tasks at a time. See your remaining allowance and usage in Settings."
)
BODY_RU = (
    "Лимит ИИ стал больше. В обычной переписке больше не нужно подтверждать "
    "максимальный расход перед каждым ответом: из лимита списывается фактический расход.\n\n"
    "При поиске ИИ может уточнять уже найденные результаты. Для длинных ответов "
    "теперь больше места. Если ответ достигнет ограничения по длине, ИИ сможет "
    "продолжить его ещё один раз, если хватит ресурсов.\n\n"
    "Можно выполнять до двух задач одновременно. Остаток лимита и историю расхода смотрите в настройках."
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
