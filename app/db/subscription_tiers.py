import uuid
from datetime import datetime, UTC
from enum import Enum
from typing import Optional, Literal, List
from sqlalchemy import Column, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field, Relationship


class SubscriptionStatus(str, Enum):
    active = "active"
    cancelled = "cancelled"
    expired = "expired"

class UsagePackSource(str, Enum):
    paid = "paid"
    free = "free"


class UsagePackStatus(str, Enum):
    active = "active"
    expired = "expired"



class SubscriptionTier(SQLModel, table=True):

    __tablename__ = "subscription_tier"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(index=True, unique=True)
    name_ru: str = Field(unique=True, nullable=True)
    description: Optional[str] = None
    description_ru: Optional[str] = Field(nullable=True)
    price_cents: int = Field(default=0)
    # feature caps (requests, not tokens)
    monthly_images: int = Field(default=0)
    daily_image_limit: int = Field(default=0)
    monthly_docs: int = Field(default=0)
    monthly_deepsearch: int = Field(default=0)
    is_active: bool = Field(default=True)
    is_public: bool = Field(default=True, nullable=False)
    index: int = Field(default=0, nullable=False)
    is_recurring: bool = Field(default=True)

    user_subscriptions: List["UserSubscription"] = Relationship(back_populates="tier")
    tier_model_limits: List["TierModelLimit"] = Relationship(back_populates="tier")
    tier_image_model_limits: List["TierImageModelLimit"] = Relationship(back_populates="tier")
    access_code: List["AccessCode"] = Relationship(back_populates="tier")
    access_code_discount: List["AccessCodeDiscount"] = Relationship(back_populates="tier")

class UsagePack(SQLModel, table=True):

    __tablename__ = "usage_pack"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(index=True, unique=True)
    name_ru: Optional[str] = Field(unique=True, nullable=True)
    description: Optional[str] = None
    description_ru: Optional[str] = Field(nullable=True)
    price_cents: int = Field(default=0)
    is_active: bool = Field(default=True)
    is_public: bool = Field(default=True, nullable=False)
    index: int = Field(default=0, nullable=False)

    pack_model_limits: List["UsagePackModelLimit"] = Relationship(back_populates="pack")
    pack_image_model_limits: List["UsagePackImageModelLimit"] = Relationship(back_populates="pack")
    user_packs: List["UserUsagePack"] = Relationship(back_populates="pack")
    access_codes: List["AccessCode"] = Relationship(back_populates="usage_pack")


class UsagePackModelLimit(SQLModel, table=True):

    __tablename__ = "usage_pack_model_limit"

    """Fixed request credits per (pack, model)."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pack_id: uuid.UUID = Field(foreign_key="usage_pack.id", index=True)
    model_name: str = Field(index=True)
    request_credits: int = Field(default=0)
    __table_args__ = (UniqueConstraint("pack_id", "model_name", name="uq_usage_pack_model"),)

    pack: UsagePack = Relationship(back_populates="pack_model_limits")


class UsagePackImageModelLimit(SQLModel, table=True):

    __tablename__ = "usage_pack_image_model_limit"

    """Fixed credit amounts per (pack, image model)."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pack_id: uuid.UUID = Field(foreign_key="usage_pack.id", index=True)
    image_model: str = Field(index=True)
    credit_amount: float = Field(default=0.0)
    __table_args__ = (UniqueConstraint("pack_id", "image_model", name="uq_usage_pack_image_model"),)

    pack: UsagePack = Relationship(back_populates="pack_image_model_limits")


class UserUsagePack(SQLModel, table=True):

    __tablename__ = "user_usage_pack"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    pack_id: uuid.UUID = Field(foreign_key="usage_pack.id", index=True)
    source: UsagePackSource = Field(default=UsagePackSource.paid)
    status: UsagePackStatus = Field(default=UsagePackStatus.active)
    purchased_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, index=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    payment_id: Optional[uuid.UUID] = Field(default=None, foreign_key="payment.id")
    note: Optional[str] = None

    pack: UsagePack = Relationship(back_populates="user_packs")

class TierModelLimit(SQLModel, table=True):

    __tablename__ = "tier_model_limit"

    """Monthly request caps per (tier, model)."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tier_id: uuid.UUID = Field(foreign_key="subscription_tier.id", index=True)
    model_name: str = Field(index=True)
    monthly_requests: int = Field(default=0)
    __table_args__ = (UniqueConstraint("tier_id", "model_name", name="uq_tier_model"),)

    tier: SubscriptionTier = Relationship(back_populates="tier_model_limits")

class TierImageModelLimit(SQLModel, table=True):

    __tablename__ = "tier_image_model_limit"

    """Monthly request caps per (tier, image model)."""
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tier_id: uuid.UUID = Field(foreign_key="subscription_tier.id", index=True)
    image_model: str = Field(index=True)
    monthly_requests: int = Field(default=0)
    __table_args__ = (UniqueConstraint("tier_id", "image_model", name="uq_tier_image_model"),)

    tier: SubscriptionTier = Relationship(back_populates="tier_image_model_limits")

class UserSubscription(SQLModel, table=True):

    __tablename__ = "user_subscription"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    tier_id: uuid.UUID = Field(foreign_key="subscription_tier.id", index=True)
    status: SubscriptionStatus = Field(default=SubscriptionStatus.active)
    started_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, index=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))

    tier: SubscriptionTier = Relationship(back_populates="user_subscriptions")
class AccessCode(SQLModel, table=True):

    __tablename__ = "access_code"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(unique=True, index=True)
    tier_id: Optional[uuid.UUID] = Field(foreign_key="subscription_tier.id") # tier to grant on redeem
    usage_pack_id: Optional[uuid.UUID] = Field(default=None, foreign_key="usage_pack.id")
    tier_expires_in_days: int = Field(default=7)
    max_uses: int = Field(default=1)
    used_count: int = Field(default=0)
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    note: Optional[str] = None
    created_by_user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="app_user.id")

    discounts: List["AccessCodeDiscount"] = Relationship(back_populates="access_code")

    tier: SubscriptionTier = Relationship(back_populates="access_code")
    usage_pack: Optional[UsagePack] = Relationship(back_populates="access_codes")
    user_tier_discounts: List["UserTierDiscount"] = Relationship(back_populates="access_code")


class AccessCodeDiscount(SQLModel, table=True):
    __tablename__ = "access_code_discounts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    access_code_id: uuid.UUID = Field(foreign_key="access_code.id")
    tier_id: uuid.UUID = Field(foreign_key="subscription_tier.id")
    discount_percent: int = Field(default=0)
    duration_months: int = Field(default=1)


    access_code: AccessCode = Relationship(back_populates="discounts")
    tier: SubscriptionTier = Relationship(back_populates="access_code_discount")


class Referral(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inviter_user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    invitee_user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    access_code_id: Optional[uuid.UUID] = Field(default=None, foreign_key="access_code.id")
    reward_applied: bool = Field(default=False)
    __table_args__ = (UniqueConstraint("invitee_user_id", name="uq_referral_unique_invitee"),)


class UserTierDiscount(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    tier_id: uuid.UUID = Field(foreign_key="subscription_tier.id")
    discount_percent: int = Field(default=0)
    valid_until: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, index=True))
    access_code_id: Optional[uuid.UUID] = Field(default=None, foreign_key="access_code.id")

    access_code: Optional[AccessCode] = Relationship(back_populates="user_tier_discounts")
