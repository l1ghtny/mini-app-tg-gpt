"""seed general_discount rows

Seeds two rows:
  - FIRST_PURCHASE: 20% off all tiers, condition no_prior_paid_sub, never expires
  - SEASONAL: inactive placeholder (0%), to be activated via direct DB update

Revision ID: gd2a3b4c5d6e
Revises: gd1a2b3c4d5e
Create Date: 2026-05-23 16:31:00.000000

"""
from typing import Sequence, Union
import uuid
from datetime import datetime, UTC

from alembic import op
import sqlalchemy as sa


revision: str = "gd2a3b4c5d6e"
down_revision: Union[str, None] = "gd1a2b3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _sid(label: str) -> str:
    return str(uuid.uuid5(_SEED_NS, label))


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def upgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    t = sa.Table("general_discount", meta, autoload_with=bind)

    rows = [
        {
            "id": _sid("first_purchase_discount"),
            "code": "FIRST_PURCHASE",
            "type": "first_purchase",
            "percent_off": 20,
            "applies_to_tiers": None,          # all tiers
            "conditions": {"no_prior_paid_sub": True},
            "starts_at": None,
            "expires_at": None,                # never expires
            "is_active": True,
            "stackable": True,
            "created_at": _now(),
        },
        {
            "id": _sid("seasonal_discount_placeholder"),
            "code": "SEASONAL",
            "type": "seasonal",
            "percent_off": 0,                  # fill in when activating
            "applies_to_tiers": None,
            "conditions": None,
            "starts_at": None,
            "expires_at": None,
            "is_active": False,                # inactive until manually enabled
            "stackable": True,
            "created_at": _now(),
        },
    ]

    for row in rows:
        # Idempotent: skip if already present (e.g. re-run on staging)
        existing = bind.execute(
            sa.select(t.c.id).where(t.c.id == row["id"])
        ).first()
        if existing is None:
            bind.execute(t.insert().values(**row))


def downgrade() -> None:
    bind = op.get_bind()
    meta = sa.MetaData()
    t = sa.Table("general_discount", meta, autoload_with=bind)
    bind.execute(
        t.delete().where(
            t.c.id.in_([_sid("first_purchase_discount"), _sid("seasonal_discount_placeholder")])
        )
    )
