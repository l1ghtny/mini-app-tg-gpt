from datetime import datetime, UTC
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import BigInteger, Column, Numeric, Index, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel
import uuid

class AppUser(SQLModel, table=True):
    __tablename__ = "app_user" # Explicitly name the table to avoid conflicts

    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    telegram_id: int = Field(sa_column=Column(BigInteger, unique=True, index=True))

    conversations: List["Conversation"] = Relationship(back_populates="user")

class Conversation(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(index=True, default="New Chat")
    user_id: uuid.UUID = Field(foreign_key="app_user.id")
    model: str = Field(default="gpt-5-nano")
    system_prompt: Optional[str] = Field(default="You are a helpful assistant.")


    user: AppUser = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


class Message(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversation.id")
    role: str

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
    created_at: datetime = Field(default_factory=datetime.utcnow, sa_column=Column(DateTime, index=True))

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