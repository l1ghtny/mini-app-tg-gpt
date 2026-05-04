"""swap gpt 5.4 and gpt 5.4 mini display labels

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
Create Date: 2026-05-04 12:20:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("text_model_catalog"):
        return

    op.execute(sa.text(
        """
        UPDATE text_model_catalog
        SET display_name = 'Balanced',
            display_name_ru = 'Сбалансированный',
            updated_at = now()
        WHERE provider = 'OpenAI' AND model_name = 'gpt-5.4-mini'
        """
    ))

    op.execute(sa.text(
        """
        UPDATE text_model_catalog
        SET display_name = 'Smart',
            display_name_ru = 'Умный',
            updated_at = now()
        WHERE provider = 'OpenAI' AND model_name = 'gpt-5.4'
        """
    ))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("text_model_catalog"):
        return

    op.execute(sa.text(
        """
        UPDATE text_model_catalog
        SET display_name = 'Smart',
            display_name_ru = 'Умный',
            updated_at = now()
        WHERE provider = 'OpenAI' AND model_name = 'gpt-5.4-mini'
        """
    ))

    op.execute(sa.text(
        """
        UPDATE text_model_catalog
        SET display_name = 'Balanced',
            display_name_ru = 'Сбалансированный',
            updated_at = now()
        WHERE provider = 'OpenAI' AND model_name = 'gpt-5.4'
        """
    ))
