"""add_google_image_resolution_and_conversation_image_size

Revision ID: h1a2b3c4d5e6
Revises: gs2a3b4c5d6e
Create Date: 2026-05-24 00:00:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = "h1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "gs2a3b4c5d6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("8c53f08e-6ab5-4364-b6f5-941c6d3b2d6e")
GOOGLE_IMAGE_MODELS = (
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
)


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def upgrade() -> None:
    op.add_column(
        "conversation",
        sa.Column("image_size", sa.String(), nullable=False, server_default="1k"),
    )

    bind = op.get_bind()
    image_quality_pricing = sa.Table(
        "image_quality_pricing",
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("image_model", sa.String()),
        sa.Column("quality", sa.String()),
        sa.Column("credit_cost", sa.Float()),
        sa.Column("description", sa.String()),
        sa.Column("description_ru", sa.String()),
        sa.Column("is_active", sa.Boolean()),
    )

    bind.execute(
        sa.text(
            "DELETE FROM image_quality_pricing WHERE image_model IN :models"
        ).bindparams(sa.bindparam("models", expanding=True)),
        {"models": GOOGLE_IMAGE_MODELS},
    )

    google_resolution_rows = [
        ("gemini-2.5-flash-image", "512", 1.0, "512 resolution"),
        ("gemini-2.5-flash-image", "1k", 2.0, "1k resolution"),
        ("gemini-2.5-flash-image", "2k", 4.0, "2k resolution"),
        ("gemini-3.1-flash-image-preview", "512", 1.0, "512 resolution"),
        ("gemini-3.1-flash-image-preview", "1k", 2.0, "1k resolution"),
        ("gemini-3.1-flash-image-preview", "2k", 4.0, "2k resolution"),
        ("gemini-3-pro-image-preview", "512", 1.0, "512 resolution"),
        ("gemini-3-pro-image-preview", "1k", 2.0, "1k resolution"),
        ("gemini-3-pro-image-preview", "2k", 4.0, "2k resolution"),
    ]

    for model_name, resolution, cost, description in google_resolution_rows:
        row = {
            "id": _stable_uuid(f"image-resolution:{model_name}:{resolution}"),
            "image_model": model_name,
            "quality": resolution,
            "credit_cost": cost,
            "description": description,
            "description_ru": None,
            "is_active": True,
        }
        insert_stmt = pg_insert(image_quality_pricing).values(**row)
        bind.execute(
            insert_stmt.on_conflict_do_update(
                index_elements=[image_quality_pricing.c.id],
                set_={
                    "image_model": insert_stmt.excluded.image_model,
                    "quality": insert_stmt.excluded.quality,
                    "credit_cost": insert_stmt.excluded.credit_cost,
                    "description": insert_stmt.excluded.description,
                    "description_ru": insert_stmt.excluded.description_ru,
                    "is_active": insert_stmt.excluded.is_active,
                },
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM image_quality_pricing WHERE image_model IN :models"
        ).bindparams(sa.bindparam("models", expanding=True)),
        {"models": GOOGLE_IMAGE_MODELS},
    )

    op.drop_column("conversation", "image_size")
