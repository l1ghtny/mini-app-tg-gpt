"""sync onboarding state across authenticated surfaces

Revision ID: x1a2b3c4d5e6
Revises: w1a2b3c4d5e6
Create Date: 2026-07-31 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "x1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "w1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column(
            "onboarding_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE app_user
            SET onboarding_state = jsonb_build_object(
                'welcome', jsonb_build_object('seen_at', to_jsonb(CURRENT_TIMESTAMP)),
                'try_folders', jsonb_build_object('dismissed_at', to_jsonb(CURRENT_TIMESTAMP)),
                'open_on_desktop', jsonb_build_object('dismissed_at', to_jsonb(CURRENT_TIMESTAMP)),
                'desktop_fullscreen_hint', jsonb_build_object('dismissed_at', to_jsonb(CURRENT_TIMESTAMP))
            )
            WHERE has_sent_first_message IS TRUE
            """
        )
    )


def downgrade() -> None:
    op.drop_column("app_user", "onboarding_state")
