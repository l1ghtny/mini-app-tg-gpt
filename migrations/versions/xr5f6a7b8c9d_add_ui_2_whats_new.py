"""Publish the three bilingual Lightny 2.0.0 announcements."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alembic import op
import sqlalchemy as sa

revision = "xr5f6a7b8c9d"
down_revision = "xq4e5f6a7b8c"
branch_labels = None
depends_on = None

PUBLISHED_AT = datetime(2026, 9, 14, 12, 38, 19)
# Stable IDs make retries safe; the timestamps keep the approved reading order.
ITEMS = (
    {
        "id": "2026-09-14-lightny-2-ui",
        "title_en": "Lightny 2.0.0 — a new look",
        "title_ru": "Lightny 2.0.0 — обновлённый интерфейс",
        "body_en": "Start with a question or choose a task: write, research, analyse a document, or "
        "create an image. We’ve refreshed both themes, screen transitions, and message "
        "layouts. On mobile, swipe right to open your chats.",
        "body_ru": "Начните с вопроса или выберите задачу: написать текст, найти информацию, "
        "разобрать документ или создать изображение. Мы обновили светлую и тёмную темы, "
        "переходы между экранами и оформление сообщений. На телефоне список чатов теперь "
        "можно открыть свайпом вправо.",
        "pinned": True,
    },
    {
        "id": "2026-09-14-clearer-chat-settings",
        "title_en": "Clearer chat settings",
        "title_ru": "Настройки чата стали понятнее",
        "body_en": "Models, tools, and image options now have their own sections. Let the assistant "
        "choose its tools, or require a specific tool for the next reply. Available "
        "choices depend on the model, with explanations beside the controls.",
        "body_ru": "Выбор модели, инструменты и параметры изображений теперь собраны в отдельные "
        "разделы. Можно доверить выбор инструментов ассистенту или указать, какой "
        "обязательно использовать в следующем ответе. Доступные варианты зависят от модели "
        "— пояснения есть рядом с выбором.",
        "pinned": False,
    },
    {
        "id": "2026-09-14-messaging-documents",
        "title_en": "Smoother messaging and document attachments",
        "title_ru": "Удобнее отправлять сообщения и документы",
        "body_en": "The composer clears as soon as you send, and your draft is restored if sending "
        "fails. Stop cancels generation and marks the reply as stopped. The document "
        "picker now has a Done button showing how many files you’ve selected.",
        "body_ru": "После отправки текст сразу исчезает из поля ввода. Если отправить сообщение не "
        "удалось, черновик восстанавливается. Кнопка остановки прерывает создание ответа, "
        "а незавершённый ответ получает отметку. В окне выбора документов появилась кнопка "
        "«Готово» с количеством выбранных файлов.",
        "pinned": False,
    },
)


def upgrade() -> None:
    now = datetime.now(UTC).replace(tzinfo=None)
    statement = sa.text(
        """
            INSERT INTO whats_new_item (
                id, kind, title_en, title_ru, body_en, body_ru,
                icon, image_url, cta_label_en, cta_label_ru, cta_kind, cta_value,
                audience_plans, min_app_version, pinned, starts_at, expires_at,
                published_at, is_active, created_at, updated_at
            ) VALUES (
                :id, 'improvement', :title_en, :title_ru, :body_en, :body_ru,
                'wrench', NULL, NULL, NULL, NULL, NULL,
                '[]'::jsonb, NULL, :pinned, NULL, NULL,
                :published_at, true, :now, :now
            )
            ON CONFLICT (id) DO UPDATE SET
                kind = EXCLUDED.kind,
                title_en = EXCLUDED.title_en,
                title_ru = EXCLUDED.title_ru,
                body_en = EXCLUDED.body_en,
                body_ru = EXCLUDED.body_ru,
                icon = EXCLUDED.icon,
                image_url = EXCLUDED.image_url,
                cta_label_en = EXCLUDED.cta_label_en,
                cta_label_ru = EXCLUDED.cta_label_ru,
                cta_kind = EXCLUDED.cta_kind,
                cta_value = EXCLUDED.cta_value,
                audience_plans = EXCLUDED.audience_plans,
                min_app_version = EXCLUDED.min_app_version,
                pinned = EXCLUDED.pinned,
                starts_at = EXCLUDED.starts_at,
                expires_at = EXCLUDED.expires_at,
                published_at = EXCLUDED.published_at,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at
            """
    )
    for index, item in enumerate(ITEMS):
        params = [
            sa.bindparam(
                key,
                value=item[key],
                type_=sa.String()
                if key.startswith("title") or key == "id"
                else sa.Text(),
            )
            for key in ("id", "title_en", "title_ru", "body_en", "body_ru")
        ]
        op.execute(
            statement.bindparams(
                *params,
                sa.bindparam("pinned", value=item["pinned"], type_=sa.Boolean()),
                sa.bindparam(
                    "published_at",
                    value=PUBLISHED_AT - timedelta(seconds=index),
                    type_=sa.DateTime(),
                ),
                sa.bindparam("now", value=now, type_=sa.DateTime()),
            )
        )


def downgrade() -> None:
    for item in ITEMS:
        op.execute(
            sa.text("DELETE FROM whats_new_item WHERE id = :id").bindparams(
                sa.bindparam("id", value=item["id"], type_=sa.String()),
            )
        )
