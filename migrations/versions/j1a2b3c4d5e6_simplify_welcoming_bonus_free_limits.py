"""simplify welcoming bonus free limits

Revision ID: j1a2b3c4d5e6
Revises: i1a2b3c4d5e6
Create Date: 2026-06-12 16:30:00.000000
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert


# revision identifiers, used by Alembic.
revision: str = "j1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "i1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SEED_NAMESPACE = uuid.UUID("92b91609-d696-42df-b4da-e6d97ec8d2c1")
WELCOMING_BONUS_NAME = "Welcoming Bonus"

FAST_BUCKET_LIMITS = {
    "gpt-5.4-nano": (15, 15),
    "gemini-3.1-flash-lite": (15, 15),
}

DISABLED_TEXT_MODELS = {
    "gpt-5.4-mini": (0, 0),
    "gemini-3.5-flash": (0, 0),
    "gpt-5.4": (0, 0),
    "gpt-5.5": (0, 0),
    "gemini-3.1-pro-preview": (0, 0),
}


def _stable_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, label)


def _welcoming_bonus_id(bind: sa.Connection) -> uuid.UUID | None:
    row = bind.execute(
        sa.text(
            """
            SELECT id
            FROM subscription_tier
            WHERE name = :name
            LIMIT 1
            """
        ),
        {"name": WELCOMING_BONUS_NAME},
    ).first()
    if not row or not row.id:
        return None
    return row.id


def _upsert_limit(
    bind: sa.Connection,
    tier_model_limit: sa.Table,
    *,
    tier_id: uuid.UUID,
    model_name: str,
    monthly_requests: int,
    daily_requests: int,
) -> None:
    insert_stmt = pg_insert(tier_model_limit).values(
        id=_stable_uuid(f"welcoming-bonus:{model_name}"),
        tier_id=tier_id,
        model_name=model_name,
        monthly_requests=monthly_requests,
        daily_requests=daily_requests,
    )
    bind.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=[tier_model_limit.c.tier_id, tier_model_limit.c.model_name],
            set_={
                "monthly_requests": insert_stmt.excluded.monthly_requests,
                "daily_requests": insert_stmt.excluded.daily_requests,
            },
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    welcoming_bonus_id = _welcoming_bonus_id(bind)
    if welcoming_bonus_id is None:
        return

    tier_model_limit = sa.Table(
        "tier_model_limit",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
        sa.Column("daily_requests", sa.Integer()),
    )

    for model_name, (monthly_requests, daily_requests) in {
        **FAST_BUCKET_LIMITS,
        **DISABLED_TEXT_MODELS,
    }.items():
        _upsert_limit(
            bind,
            tier_model_limit,
            tier_id=welcoming_bonus_id,
            model_name=model_name,
            monthly_requests=monthly_requests,
            daily_requests=daily_requests,
        )


def downgrade() -> None:
    bind = op.get_bind()
    welcoming_bonus_id = _welcoming_bonus_id(bind)
    if welcoming_bonus_id is None:
        return

    tier_model_limit = sa.Table(
        "tier_model_limit",
        sa.MetaData(),
        sa.Column("id", sa.Uuid()),
        sa.Column("tier_id", sa.Uuid()),
        sa.Column("model_name", sa.String()),
        sa.Column("monthly_requests", sa.Integer()),
        sa.Column("daily_requests", sa.Integer()),
    )

    rollback_limits = {
        "gpt-5.4-nano": (100, 25),
        "gemini-3.1-flash-lite": (100, 25),
        "gpt-5.4-mini": (20, 0),
        "gemini-3.5-flash": (20, 0),
        "gpt-5.4": (10, 0),
        "gpt-5.5": (5, 0),
        "gemini-3.1-pro-preview": (5, 0),
    }

    for model_name, (monthly_requests, daily_requests) in rollback_limits.items():
        _upsert_limit(
            bind,
            tier_model_limit,
            tier_id=welcoming_bonus_id,
            model_name=model_name,
            monthly_requests=monthly_requests,
            daily_requests=daily_requests,
        )
