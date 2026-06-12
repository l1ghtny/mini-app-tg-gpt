"""reprice google image energy costs

Revision ID: g2a3b4c5d6e7
Revises: z1a2b3c4d5e6
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "z1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GOOGLE_IMAGE_ROWS = (
    (
        "gemini-2.5-flash-image",
        "1k",
        50.0,
        "1k resolution (~$0.039/image)",
        None,
    ),
    (
        "gemini-3.1-flash-image-preview",
        "512",
        50.0,
        "512 resolution (~$0.045/image)",
        None,
    ),
    (
        "gemini-3.1-flash-image-preview",
        "1k",
        80.0,
        "1k resolution (~$0.067/image)",
        None,
    ),
    (
        "gemini-3.1-flash-image-preview",
        "2k",
        120.0,
        "2k resolution (~$0.101/image)",
        None,
    ),
    (
        "gemini-3-pro-image-preview",
        "512",
        150.0,
        "512 resolution (priced conservatively at ~$0.134/image)",
        None,
    ),
    (
        "gemini-3-pro-image-preview",
        "1k",
        150.0,
        "1k resolution (~$0.134/image)",
        None,
    ),
    (
        "gemini-3-pro-image-preview",
        "2k",
        150.0,
        "2k resolution (~$0.134/image)",
        None,
    ),
)

GOOGLE_IMAGE_ROWS_TO_DISABLE = (
    ("gemini-2.5-flash-image", "512"),
    ("gemini-2.5-flash-image", "2k"),
)


def upgrade() -> None:
    for image_model, quality, credit_cost, description, description_ru in GOOGLE_IMAGE_ROWS:
        op.execute(
            sa.text(
                """
                UPDATE image_quality_pricing
                SET credit_cost = :credit_cost,
                    description = :description,
                    description_ru = :description_ru,
                    is_active = true
                WHERE image_model = :image_model
                  AND quality = :quality
                """
            ),
            {
                "image_model": image_model,
                "quality": quality,
                "credit_cost": credit_cost,
                "description": description,
                "description_ru": description_ru,
            },
        )
    for image_model, quality in GOOGLE_IMAGE_ROWS_TO_DISABLE:
        op.execute(
            sa.text(
                """
                UPDATE image_quality_pricing
                SET is_active = false
                WHERE image_model = :image_model
                  AND quality = :quality
                """
            ),
            {
                "image_model": image_model,
                "quality": quality,
            },
        )


def downgrade() -> None:
    rollback_rows = (
        ("gemini-2.5-flash-image", "512", 1.0, "512 resolution"),
        ("gemini-2.5-flash-image", "1k", 2.0, "1k resolution"),
        ("gemini-2.5-flash-image", "2k", 4.0, "2k resolution"),
        ("gemini-3.1-flash-image-preview", "512", 1.0, "512 resolution"),
        ("gemini-3.1-flash-image-preview", "1k", 2.0, "1k resolution"),
        ("gemini-3.1-flash-image-preview", "2k", 4.0, "2k resolution"),
        ("gemini-3-pro-image-preview", "512", 1.0, "512 resolution"),
        ("gemini-3-pro-image-preview", "1k", 2.0, "1k resolution"),
        ("gemini-3-pro-image-preview", "2k", 4.0, "2k resolution"),
    )
    for image_model, quality, credit_cost, description in rollback_rows:
        op.execute(
            sa.text(
                """
                UPDATE image_quality_pricing
                SET credit_cost = :credit_cost,
                    description = :description,
                    description_ru = NULL,
                    is_active = true
                WHERE image_model = :image_model
                  AND quality = :quality
                """
            ),
            {
                "image_model": image_model,
                "quality": quality,
                "credit_cost": credit_cost,
                "description": description,
            },
        )
    for image_model, quality in GOOGLE_IMAGE_ROWS_TO_DISABLE:
        op.execute(
            sa.text(
                """
                UPDATE image_quality_pricing
                SET is_active = true
                WHERE image_model = :image_model
                  AND quality = :quality
                """
            ),
            {
                "image_model": image_model,
                "quality": quality,
            },
        )
