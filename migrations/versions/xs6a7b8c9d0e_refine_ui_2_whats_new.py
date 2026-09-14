"""Shorten the Lightny 2.0 overview and label it as a new feature."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xs6a7b8c9d0e"
down_revision = "xr5f6a7b8c9d"
branch_labels = None
depends_on = None

ITEM_ID = "2026-09-14-lightny-2-ui"
BODY_EN = (
    "We’ve refreshed both themes, screen transitions, and message layouts. "
    "On mobile, swipe right to open your chats."
)
BODY_RU = (
    "Мы обновили светлую и тёмную темы, переходы между экранами и оформление "
    "сообщений. На телефоне список чатов теперь можно открыть свайпом вправо."
)


def _update(*, title_en, title_ru, body_en, body_ru, kind, icon):
    op.execute(
        sa.text(
            """
            UPDATE whats_new_item
            SET title_en = :title_en, title_ru = :title_ru,
                body_en = :body_en, body_ru = :body_ru,
                kind = :kind, icon = :icon, updated_at = :now
            WHERE id = :id
            """
        ).bindparams(
            sa.bindparam("id", value=ITEM_ID, type_=sa.String()),
            sa.bindparam("title_en", value=title_en, type_=sa.String()),
            sa.bindparam("title_ru", value=title_ru, type_=sa.String()),
            sa.bindparam("body_en", value=body_en, type_=sa.Text()),
            sa.bindparam("body_ru", value=body_ru, type_=sa.Text()),
            sa.bindparam("kind", value=kind, type_=sa.String()),
            sa.bindparam("icon", value=icon, type_=sa.String()),
            sa.bindparam(
                "now", value=datetime.now(UTC).replace(tzinfo=None), type_=sa.DateTime()
            ),
        )
    )


def upgrade() -> None:
    _update(
        title_en="Lightny 2.0 — a new look",
        title_ru="Lightny 2.0 — обновлённый интерфейс",
        body_en=BODY_EN,
        body_ru=BODY_RU,
        kind="feature",
        icon="circle-plus",
    )


def downgrade() -> None:
    _update(
        title_en="Lightny 2.0.0 — a new look",
        title_ru="Lightny 2.0.0 — обновлённый интерфейс",
        body_en=(
            "Start with a question or choose a task: write, research, analyse a document, "
            "or create an image. " + BODY_EN
        ),
        body_ru=(
            "Начните с вопроса или выберите задачу: написать текст, найти информацию, "
            "разобрать документ или создать изображение. " + BODY_RU
        ),
        kind="improvement",
        icon="wrench",
    )
