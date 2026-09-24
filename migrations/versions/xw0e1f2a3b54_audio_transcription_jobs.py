"""Persist bounded audio transcription jobs and recoverable results."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "xw0e1f2a3b54"
down_revision = "xw0e1f2a3b53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audio_transcription_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("audio_key", sa.String(), nullable=True),
        sa.Column("audio_extension", sa.String(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("attempt_started_at", sa.DateTime(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "request_id", name="uq_audio_transcription_job_user_request"),
    )
    for column in ("user_id", "request_id", "channel", "status", "lease_expires_at", "created_at", "expires_at"):
        op.create_index(f"ix_audio_transcription_job_{column}", "audio_transcription_job", [column])
    op.create_index("ix_audio_transcription_job_status_created", "audio_transcription_job", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("audio_transcription_job")
