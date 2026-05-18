"""add telegram profile fields to app_user

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-17 11:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("telegram_username", sa.String(), nullable=True))
    op.add_column("app_user", sa.Column("telegram_first_name", sa.String(), nullable=True))
    op.add_column("app_user", sa.Column("telegram_last_name", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_app_user_telegram_username"),
        "app_user",
        ["telegram_username"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_user_telegram_username"), table_name="app_user")
    op.drop_column("app_user", "telegram_last_name")
    op.drop_column("app_user", "telegram_first_name")
    op.drop_column("app_user", "telegram_username")

