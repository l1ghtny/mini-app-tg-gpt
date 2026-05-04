"""add gpt-5.5 to all tiers with zero usage

Revision ID: ab55b2c9d1f0
Revises: e31a7f88b9d2
Create Date: 2026-05-04 11:35:00.000000

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ab55b2c9d1f0"
down_revision: Union[str, Sequence[str], None] = "e31a7f88b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MODEL_NAME = "gpt-5.5"
SEED_NAMESPACE = uuid.UUID("d5f9f4dc-eebd-4b9e-b83b-d8f4a145a3db")


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("subscription_tier") or not inspector.has_table("tier_model_limit"):
        return

    metadata = sa.MetaData()
    subscription_tier = sa.Table(
        "subscription_tier",
        metadata,
        sa.Column("id", sa.Uuid()),
    )
    tier_model_limit = sa.Table(
        "tier_model_limit",
        metadata,
        sa.Column("id", sa.Uuid()),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
    )

    tier_ids = list(bind.execute(sa.select(subscription_tier.c.id)).scalars())
    if not tier_ids:
        return

    existing_tier_ids = set(
        bind.execute(
            sa.select(tier_model_limit.c.tier_id).where(
                tier_model_limit.c.model_name == MODEL_NAME
            )
        ).scalars()
    )

    missing_tier_ids = [tier_id for tier_id in tier_ids if tier_id not in existing_tier_ids]
    if not missing_tier_ids:
        return

    rows = [
        {
            "id": _stable_uuid(f"tier-model:{tier_id}:{MODEL_NAME}:zero"),
            "tier_id": tier_id,
            "model_name": MODEL_NAME,
            "monthly_requests": 0,
        }
        for tier_id in missing_tier_ids
    ]
    op.execute(sa.insert(tier_model_limit), rows)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("tier_model_limit"):
        return

    metadata = sa.MetaData()
    tier_model_limit = sa.Table(
        "tier_model_limit",
        metadata,
        sa.Column("id", sa.Uuid()),
    )

    tier_ids_stmt = sa.text(
        "SELECT id FROM subscription_tier"
    )
    tier_ids = [row[0] for row in bind.execute(tier_ids_stmt)]
    if not tier_ids:
        return

    seeded_ids = [_stable_uuid(f"tier-model:{tier_id}:{MODEL_NAME}:zero") for tier_id in tier_ids]
    op.execute(
        sa.delete(tier_model_limit).where(
            tier_model_limit.c.id.in_(seeded_ids)
        )
    )
