"""add workflow attribution and cache-aware cost telemetry

Revision ID: q1a2b3c4d5e6
Revises: o1a2b3c4d5e6
Create Date: 2026-07-27 16:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "q1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "o1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("request_ledger", sa.Column("workflow_kind", sa.String(), nullable=True))
    op.create_index("ix_request_ledger_workflow_kind", "request_ledger", ["workflow_kind"])

    op.create_table(
        "chat_folder_document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("folder_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("attached_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["folder_id"], ["chat_folder.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["user_document.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("folder_id", "document_id", name="uq_chat_folder_document"),
    )
    op.create_index("ix_chat_folder_document_folder_id", "chat_folder_document", ["folder_id"])
    op.create_index("ix_chat_folder_document_document_id", "chat_folder_document", ["document_id"])
    op.create_index("ix_chat_folder_document_attached_at", "chat_folder_document", ["attached_at"])

    op.add_column(
        "aimodelpricing",
        sa.Column("unit_price_cached_input_per_1m", sa.Numeric(18, 6), nullable=True),
    )
    op.add_column(
        "aimodelpricing",
        sa.Column("unit_price_cache_write_per_1m", sa.Numeric(18, 6), nullable=True),
    )
    # GPT-5.6 cache reads cost 10% of normal input; cache writes cost 125%.
    op.execute(
        sa.text(
            """
            UPDATE aimodelpricing
            SET unit_price_cached_input_per_1m = unit_price_input_per_1m * 0.10,
                unit_price_cache_write_per_1m = unit_price_input_per_1m * 1.25
            WHERE provider = 'openai'
              AND model_name IN ('gpt-5.6-luna', 'gpt-5.6-terra', 'gpt-5.6-sol')
            """
        )
    )

    op.add_column("tokenusage", sa.Column("cached_input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tokenusage", sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "tokenusage",
        sa.Column("cost_cached_input", sa.Numeric(18, 6), nullable=False, server_default="0"),
    )
    op.add_column(
        "tokenusage",
        sa.Column("cost_cache_write", sa.Numeric(18, 6), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("tokenusage", "cost_cache_write")
    op.drop_column("tokenusage", "cost_cached_input")
    op.drop_column("tokenusage", "cache_write_tokens")
    op.drop_column("tokenusage", "cached_input_tokens")
    op.drop_column("aimodelpricing", "unit_price_cache_write_per_1m")
    op.drop_column("aimodelpricing", "unit_price_cached_input_per_1m")
    op.drop_index("ix_chat_folder_document_attached_at", table_name="chat_folder_document")
    op.drop_index("ix_chat_folder_document_document_id", table_name="chat_folder_document")
    op.drop_index("ix_chat_folder_document_folder_id", table_name="chat_folder_document")
    op.drop_table("chat_folder_document")
    op.drop_index("ix_request_ledger_workflow_kind", table_name="request_ledger")
    op.drop_column("request_ledger", "workflow_kind")
