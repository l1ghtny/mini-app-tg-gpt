"""Stage the combined audio transcription and limits announcement."""

from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa

revision = "xw0e1f2a3b56"
down_revision = "xw0e1f2a3b55"
branch_labels = None
depends_on = None

ITEM_ID = "2026-09-25-audio-transcription"
TITLE_EN = "Turn audio files into text"
TITLE_RU = "Расшифровка аудиофайлов"
BODY_EN = (
    "Open Add attachment → Transcribe audio to upload an MP3, M4A, WAV or WebM file "
    "up to 30 minutes and 20 MB. Edit or copy the transcript, download it as a TXT "
    "file, or add it to your chat draft.\n\n"
    "Before you start, check the estimated usage and your remaining minutes. "
    "Subscription → Usage shows your monthly allowance and when it resets. "
    "Files and dictation share a separate minute allowance. "
    "Available on plans that include transcription."
)
BODY_RU = (
    "Нажмите «Добавить вложение» → «Расшифровать аудио» и загрузите файл MP3, M4A, "
    "WAV или WebM длительностью до 30 минут и размером до 20 МБ. Готовый текст "
    "можно отредактировать, скопировать, скачать в TXT или добавить в черновик "
    "сообщения.\n\n"
    "Перед началом вы увидите примерный расход и остаток минут. В разделе "
    "«Подписка» → «Расход» указаны месячный лимит и время его обновления. "
    "Файлы и диктовка расходуют общий запас минут, отдельно от лимита ИИ. "
    "Расшифровка доступна на тарифах, в которые она входит."
)


def upgrade():
    # Release operator activates this one item after both UI rollouts pass.
    # Keep it hidden during the shared-schema-first deployment window.
    now = datetime.now(UTC).replace(tzinfo=None)
    op.execute(
        sa.text("""
        INSERT INTO whats_new_item (
            id, kind, title_en, title_ru, body_en, body_ru,
            icon, image_url, cta_label_en, cta_label_ru, cta_kind, cta_value,
            audience_plans, min_app_version, pinned, starts_at, expires_at,
            published_at, is_active, created_at, updated_at
        ) VALUES (
            :id, 'feature', :title_en, :title_ru, :body_en, :body_ru,
            'sparkles', NULL, 'View usage', 'Посмотреть расход', 'open_subscription', 'overview',
            '[]'::jsonb, NULL, false, NULL, NULL, :now, false, :now, :now
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
