from datetime import datetime, UTC, timezone
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import BigInteger, Column, Numeric, Index, DateTime, ForeignKey, Integer, UniqueConstraint, \
    CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel
import uuid

## Helper function for default_factory
def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AppUser(SQLModel, table=True):
    __tablename__ = "app_user" # Explicitly name the table to avoid conflicts

    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    telegram_id: int = Field(sa_column=Column(BigInteger, unique=True, index=True))
    has_sent_first_message: bool = Field(default=False)
    campaign: Optional[str] = Field(default=None, index=True)

    conversations: List["Conversation"] = Relationship(back_populates="user")
    requests: List["RequestLedger"] = Relationship(back_populates="user")
    payments: List["Payment"] = Relationship(back_populates="user")

class Conversation(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(index=True, default="New Chat")
    user_id: uuid.UUID = Field(foreign_key="app_user.id")
    model: str = Field(default="gpt-5-nano")
    image_model: str = Field(default="gpt-image-1.5", nullable=True)
    system_prompt: Optional[str] = Field(default="Ты помощник, готовый ответить на вопросы.")
    image_quality: str = Field(default="low") # low, medium, high

    updated_at: datetime = Field(
        default_factory=utcnow_naive,
        sa_column=Column(DateTime, index=True, onupdate=utcnow_naive)
    )


    user: AppUser = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class Message(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversation.id")
    role: str
    created_at: datetime = Field(
        default_factory=utcnow_naive,
        sa_column=Column(DateTime, index=True)
    )

    conversation: "Conversation" = Relationship(back_populates="messages")

    # A message can now have multiple content parts
    content: List["MessageContent"] = Relationship(
        back_populates="message",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class MessageContent(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    message_id: uuid.UUID = Field(foreign_key="message.id")
    ordinal: int = Field(sa_column=Column(Integer, default=0))
    data: Optional[dict] = Field(default=None, sa_column=Column(JSONB))

    type: str  # "text" or "image_url"
    value: str  # The actual text or the URL for the image


    message: Message = Relationship(back_populates="content")


class AiModelPricing(SQLModel, table=True):
    """
    Pricing per 1,000,000 tokens for text/reasoning; per-call for search; per-image for image gen.
    """
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    provider: str = Field(index=True)  # e.g., "openai"
    model_name: str = Field(index=True)
    currency: str = Field(default="USD")

    # per-1M token prices
    unit_price_input_per_1m: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    unit_price_output_per_1m: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    unit_price_reasoning_per_1m: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))

    # per-call / per-item prices
    unit_price_web_search_call: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    unit_price_image_generation: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))

    is_active: bool = Field(default=True)

    __table_args__ = (
        Index("ix_pricing_provider_model_active", "provider", "model_name", "is_active"),
    )

class TokenUsage(SQLModel, table=True):
    """
    Ledger of usage per response/operation (no raw JSON payloads).
    """
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))

    user_id: Optional[uuid.UUID] = Field(default=None, foreign_key="app_user.id")
    conversation_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("conversation.id", ondelete="SET NULL"),
            nullable=True,
        )
    )

    provider: str = Field(default="openai", index=True)
    model_name: str = Field(index=True)

    # request correlation
    request_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="success")  # success | error | cancelled
    error_message: Optional[str] = Field(default=None)

    # usage counters
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    reasoning_tokens: int = Field(default=0)
    web_search_calls: int = Field(default=0)
    images_generated: int = Field(default=0)

    # cost breakdown (same currency as pricing)
    currency: str = Field(default="USD")
    cost_input: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    cost_output: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    cost_reasoning: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    cost_web_search: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    cost_images: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))
    total_cost: Decimal = Field(sa_column=Column(Numeric(18, 6), nullable=False, default=0))

    __table_args__ = (
        Index("ix_token_usage_user_created", "user_id", "created_at"),
    )


class DerivedImage(SQLModel, table=True):
    __tablename__ = "derived_image"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    original_key: str = Field(index=True)
    target_format: str = Field(index=True)  # "jpeg" | "png" | "webp"
    max_side: int = Field(default=2048)

    derived_key: str = Field(index=True, unique=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=Column(DateTime, index=True)
    )

    __table_args__ = (
        UniqueConstraint("original_key", "target_format", "max_side", name="uq_derived_image_variant"),
    )


class State(str, Enum):
    reserved = "reserved"
    consumed = "consumed"
    refunded = "refunded"
    failed = "failed"

class PaymentProductType(str, Enum):
    subscription = "subscription"
    usage_pack = "usage_pack"


class RequestLedger(SQLModel, table=True):

    __tablename__ = "request_ledger"
    """
    One row per billable request (text generation) or per generated image.
    Survives conversation/message deletions.
    """
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    tier_id: Optional[uuid.UUID] = Field(default=None, foreign_key="subscription_tier.id", index=True)
    usage_pack_id: Optional[uuid.UUID] = Field(default=None, foreign_key="user_usage_pack.id", index=True)
    # nullable references for diagnostics; DO NOT FK so deletes don’t cascade
    conversation_id: Optional[uuid.UUID] = Field(default=None, index=True)
    assistant_message_id: Optional[uuid.UUID] = Field(default=None, index=True)

    request_id: str = Field(index=True)  # client- or server-generated; used for idempotency
    model_name: str = Field(index=True)
    feature: str = Field(index=True)
    cost: int = Field(default=1)

    state: State = Field(default=State.reserved, index=True)
    tool_choice: Optional[str] = None     # e.g., "auto" or "image_generation"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
                                 sa_column=Column(DateTime, index=True))

    user: "AppUser" = Relationship(back_populates="requests")

    __table_args__ = (UniqueConstraint("user_id","request_id", name="uq_user_reqid"),
        CheckConstraint(
            "feature IN ('text','image','doc','deepsearch','web_search')",
            name="ck_request_feature",
        ),
    )


class Payment(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)

    # We store tier_name directly to preserve history if tiers change
    tier_name: str = Field(index=True)
    product_type: PaymentProductType = Field(default=PaymentProductType.subscription, index=True)
    pack_id: Optional[uuid.UUID] = Field(default=None, foreign_key="usage_pack.id", index=True)

    # Amount in CENTS (kopecks)
    amount: int = Field(nullable=False)
    currency: str = Field(default="RUB")

    # TBank specific fields
    tbank_payment_id: Optional[str] = Field(default=None, index=True)
    tbank_status: str = Field(default="NEW")  # NEW, CONFIRMED, REJECTED, etc.

    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)

    user: "AppUser" = Relationship()


class PaymentMethod(SQLModel, table=True):
    __tablename__ = "payment_methods"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)

    # TBank "RebillId" - the token we use to charge money later
    rebill_id: str = Field(index=True)

    # Card info for UI (e.g., "Visa •••• 4242")
    card_type: str = Field(default="Unknown")
    pan: str = Field(default="****")
    exp_date: str = Field(default="")  # MMYY

    is_default: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow_naive)

    user: "AppUser" = Relationship()


class ImageQualityPricing(SQLModel, table=True):
    """
    Defines the credit cost for different image qualities.
    Example rows:
    - quality="standard", credit_cost=1.0
    - quality="high",     credit_cost=2.0
    - quality="ultra",    credit_cost=4.0
    """
    __tablename__ = "image_quality_pricing"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    image_model: str = Field(index=True)
    quality: str = Field()  # e.g., low, medium, high
    credit_cost: float = Field(default=1.0)  # How many 'daily bucket units' this consumes
    description: Optional[str] = None  # e.g., "1024x1024, fast"
    is_active: bool = Field(default=True)


