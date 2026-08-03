"""Add transcription to request ledger features.

Revision ID: va1b2c3d4e5f
Revises: y1a2b3c4d5e6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "va1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "y1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription_tier",
        sa.Column(
            "monthly_transcription_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE subscription_tier
            SET monthly_transcription_minutes = CASE lower(name)
                WHEN 'smooth tier' THEN 120
                WHEN 'basic' THEN 60
                WHEN 'advanced' THEN 180
                WHEN 'premium' THEN 360
                ELSE 0
            END
            """
        )
    )
    op.drop_constraint("ck_request_feature", "request_ledger", type_="check")
    op.create_check_constraint(
        "ck_request_feature",
        "request_ledger",
        "feature IN ('text','image','doc','deepsearch','web_search','transcription')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_request_feature", "request_ledger", type_="check")
    op.create_check_constraint(
        "ck_request_feature",
        "request_ledger",
        "feature IN ('text','image','doc','deepsearch','web_search')",
    )
    op.drop_column("subscription_tier", "monthly_transcription_minutes")
