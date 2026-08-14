"""allow multiple artifacts per Work run

Revision ID: xf3a4b5c6d7e
Revises: xe2f3a4b5c6d
Create Date: 2026-08-12 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "xf3a4b5c6d7e"
down_revision: Union[str, Sequence[str], None] = "xe2f3a4b5c6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Relaxing this constraint is safe for the currently deployed application:
    # old code continues to create one artifact, while the general agent may
    # persist several independently versioned deliverables from one run.
    op.drop_constraint("uq_artifact_run_version", "artifact", type_="unique")
    op.create_index(
        "ix_artifact_work_run_version",
        "artifact",
        ["work_run_id", "version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_work_run_version", table_name="artifact")
    op.create_unique_constraint(
        "uq_artifact_run_version",
        "artifact",
        ["work_run_id", "version"],
    )
