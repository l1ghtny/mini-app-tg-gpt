import uuid
from datetime import datetime, UTC
from typing import Optional, Literal
from sqlalchemy import Column, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field

# Your existing tiers table — now add per-model limits in a separate table
class SubscriptionTier(SQLModel, table=True):

    __tablename__ = "subscription_tier"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: Optional[str] = None
    price_cents: int = Field(default=0)
    # feature caps (requests, not tokens)
    monthly_images: int = Field(default=0)
    monthly_docs: int = Field(default=0)
    monthly_deepsearch: int = Field(default=0)
    # optional allowlist for models (pure allowlist; limits live in TierModelLimit)
    allowed_models: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    is_active: bool = Field(default=True)

class TierModelLimit(SQLModel, table=True):

    __tablename__ = "tier_model_limit"

    """Monthly request caps per (tier, model)."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tier_id: uuid.UUID = Field(foreign_key="subscriptiontier.id", index=True)
    model_name: str = Field(index=True)
    monthly_requests: int = Field(default=0)
    __table_args__ = (UniqueConstraint("tier_id", "model_name", name="uq_tier_model"),)

class UserSubscription(SQLModel, table=True):

    __tablename__ = "user_subscription"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    tier_id: uuid.UUID = Field(foreign_key="subscriptiontier.id", index=True)
    status: Literal["active","cancelled","expired"] = Field(default="active")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
                                 sa_column=Column(DateTime, index=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    discount_percent: int = Field(default=0)
    discount_expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))

class AccessCode(SQLModel, table=True):

    __tablename__ = "access_code"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(unique=True, index=True)
    tier_id: uuid.UUID = Field(foreign_key="subscriptiontier.id")
    discount_percent: int = Field(default=0)
    discount_months: int = Field(default=0)
    max_uses: int = Field(default=1)
    used_count: int = Field(default=0)
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    note: Optional[str] = None
    created_by_user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="app_user.id")

class Referral(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inviter_user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    invitee_user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    access_code_id: Optional[uuid.UUID] = Field(default=None, foreign_key="accesscode.id")
    reward_applied: bool = Field(default=False)
    __table_args__ = (UniqueConstraint("invitee_user_id", name="uq_referral_unique_invitee"),)