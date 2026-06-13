from datetime import datetime, UTC, timezone
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlalchemy import ARRAY, BigInteger, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, \
    Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel
import uuid
import uuid6

## Helper function for default_factory
def utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AppUser(SQLModel, table=True):
    __tablename__ = "app_user" # Explicitly name the table to avoid conflicts

    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    telegram_id: int = Field(sa_column=Column(BigInteger, unique=True, index=True))
    telegram_username: Optional[str] = Field(default=None, index=True)
    telegram_first_name: Optional[str] = Field(default=None)
    telegram_last_name: Optional[str] = Field(default=None)
    has_sent_first_message: bool = Field(default=False)
    campaign: Optional[str] = Field(default=None, index=True)

    default_prompt: str = Field(default="Ты помощник, готовый ответить на вопросы.")
    default_text_model: str = Field(default="gpt-5.4-nano")
    default_image_model: str = Field(default="gpt-image-1.5")
    default_document_provider: str = Field(default="openai")
    default_thinking: bool = Field(default=True)

    conversations: List["Conversation"] = Relationship(back_populates="user")
    folders: List["ChatFolder"] = Relationship(back_populates="user")
    requests: List["RequestLedger"] = Relationship(back_populates="user")
    payments: List["Payment"] = Relationship(back_populates="user")
    documents: List["UserDocument"] = Relationship(back_populates="user")
    payment_binding_sessions: List["PaymentBindingSession"] = Relationship(back_populates="user")


class WhatsNewItem(SQLModel, table=True):
    __tablename__ = "whats_new_item"

    id: str = Field(primary_key=True)
    kind: str = Field(index=True)  # feature | improvement | fix | announcement | promo

    title_en: str
    title_ru: Optional[str] = None
    body_en: str
    body_ru: Optional[str] = None

    icon: Optional[str] = None
    image_url: Optional[str] = None

    cta_label_en: Optional[str] = None
    cta_label_ru: Optional[str] = None
    cta_kind: Optional[str] = None  # open_settings | open_subscription | open_url | dismiss
    cta_value: Optional[str] = None

    audience_plans: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    min_app_version: Optional[str] = None

    pinned: bool = Field(default=False, index=True)
    starts_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    published_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))

    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    __table_args__ = (
        CheckConstraint(
            "kind IN ('feature','improvement','fix','announcement','promo')",
            name="ck_whats_new_item_kind",
        ),
        CheckConstraint(
            "cta_kind IS NULL OR cta_kind IN ('open_settings','open_subscription','open_url','dismiss')",
            name="ck_whats_new_item_cta_kind",
        ),
    )


class UserWhatsNewState(SQLModel, table=True):
    __tablename__ = "user_whats_new_state"

    user_id: uuid.UUID = Field(foreign_key="app_user.id", primary_key=True)
    seen_up_to: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))


class UserPersonalization(SQLModel, table=True):
    __tablename__ = "user_personalization"

    user_id: uuid.UUID = Field(foreign_key="app_user.id", primary_key=True)
    answers: Optional[dict] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    completed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    dismissed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))
    updated_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True))


class ChatStarterSuggestion(SQLModel, table=True):
    __tablename__ = "chat_starter_suggestion"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    language: str = Field(index=True)  # en | ru
    text: str
    is_active: bool = Field(default=True, index=True)
    sort_index: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    __table_args__ = (
        CheckConstraint(
            "language IN ('en','ru')",
            name="ck_chat_starter_suggestion_language",
        ),
        UniqueConstraint("language", "text", name="uq_chat_starter_suggestion_language_text"),
        Index("ix_chat_starter_suggestion_active_lang", "is_active", "language"),
    )


class ChatFolder(SQLModel, table=True):
    __tablename__ = "chat_folder"

    id: uuid.UUID = Field(default_factory=uuid6.uuid7, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    name: str = Field(index=True)
    prompt: Optional[str] = Field(default=None)

    user: AppUser = Relationship(back_populates="folders")
    conversations: List["Conversation"] = Relationship(
        back_populates="folder",
        sa_relationship_kwargs={"cascade": "all"}
    )


class Conversation(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(index=True, default="New Chat")
    user_id: uuid.UUID = Field(foreign_key="app_user.id")
    folder_id: Optional[uuid.UUID] = Field(default=None, foreign_key="chat_folder.id", index=True)
    model: str = Field(default="gpt-5.4-nano")
    image_model: str = Field(default="gpt-image-1.5", nullable=True)
    image_quality: str = Field(default="low") # low, medium, high
    image_size: str = Field(default="1k")  # 512, 1k, 2k
    thinking: bool = Field(default=True)
    history_summary: Optional[str] = Field(default=None, nullable=True)
    history_summary_up_to_message_id: Optional[uuid.UUID] = Field(default=None, nullable=True)
    history_summary_updated_at: Optional[datetime] = Field(default=None, nullable=True)
    last_openai_response_id: Optional[str] = Field(default=None, nullable=True, index=True)
    openai_chain_updated_at: Optional[datetime] = Field(default=None, nullable=True)
    openai_chain_context_fingerprint: Optional[str] = Field(default=None, nullable=True)
    last_google_interaction_id: Optional[str] = Field(default=None, nullable=True, index=True)
    google_chain_updated_at: Optional[datetime] = Field(default=None, nullable=True)
    google_chain_context_fingerprint: Optional[str] = Field(default=None, nullable=True)

    updated_at: datetime = Field(
        default_factory=utcnow_naive,
        sa_column=Column(DateTime, index=True, onupdate=utcnow_naive)
    )


    user: AppUser = Relationship(back_populates="conversations")
    folder: Optional[ChatFolder] = Relationship(back_populates="conversations")
    messages: List["Message"] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    attached_documents: List["ConversationDocument"] = Relationship(
        back_populates="conversation",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class Message(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversation.id")
    role: str
    created_at: datetime = Field(
        default_factory=utcnow_naive,
        sa_column=Column(DateTime, index=True)
    )
    reasoning_summary: Optional[str] = Field(default=None)

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


class ImageAsset(SQLModel, table=True):
    __tablename__ = "image_asset"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    conversation_id: Optional[uuid.UUID] = Field(default=None, foreign_key="conversation.id", index=True)
    message_content_id: Optional[uuid.UUID] = Field(default=None, foreign_key="messagecontent.id", index=True)

    bucket: str = Field(index=True)
    key: str = Field(index=True)
    public_url: str = Field(index=True)
    source: str = Field(default="generated", index=True)  # generated | uploaded | derived
    retention_policy: str = Field(default="free_30d", index=True)
    status: str = Field(default="active", index=True)  # active | expired | missing | deleted

    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    last_checked_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))

    __table_args__ = (
        Index("ix_image_asset_user_status_expires", "user_id", "status", "expires_at"),
        Index("ix_image_asset_content", "message_content_id"),
    )


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


class TextModelCatalog(SQLModel, table=True):
    __tablename__ = "text_model_catalog"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    provider: str = Field(index=True)
    model_name: str = Field(index=True)

    display_name: str
    display_name_ru: Optional[str] = None
    tagline: Optional[str] = None
    tagline_ru: Optional[str] = None
    description: Optional[str] = None
    description_ru: Optional[str] = None

    best_for: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    best_for_ru: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    not_great_for: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    not_great_for_ru: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    speed: Optional[str] = None
    intelligence: Optional[int] = None
    context_window: Optional[int] = None

    supports: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    tier_required: Optional[dict] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    badges: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    credit_cost_hint: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(18, 6), nullable=True))

    is_active: bool = Field(default=True)
    sort_index: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    __table_args__ = (
        UniqueConstraint("provider", "model_name", name="uq_text_model_catalog_provider_model"),
        Index("ix_text_model_catalog_active_sort", "is_active", "sort_index"),
    )


class ImageModelCatalog(SQLModel, table=True):
    __tablename__ = "image_model_catalog"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    provider: str = Field(index=True)
    model_name: str = Field(index=True)

    display_name: str
    display_name_ru: Optional[str] = None
    tagline: Optional[str] = None
    tagline_ru: Optional[str] = None
    description: Optional[str] = None
    description_ru: Optional[str] = None

    best_for: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    best_for_ru: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    speed: Optional[str] = None

    tier_required: Optional[dict] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    badges: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    is_active: bool = Field(default=True)
    sort_index: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    __table_args__ = (
        UniqueConstraint("provider", "model_name", name="uq_image_model_catalog_provider_model"),
        Index("ix_image_model_catalog_active_sort", "is_active", "sort_index"),
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
    cost: float = Field(default=1.0)
    access_path: Optional[str] = Field(default=None, index=True)

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


class UserDocument(SQLModel, table=True):
    __tablename__ = "user_document"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    filename: str
    mime_type: Optional[str] = Field(default=None)
    size_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0))
    usage_bytes: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, default=0))
    sha256: Optional[str] = Field(default=None, index=True)

    status: str = Field(default="uploading", index=True)
    is_pinned: bool = Field(default=False, index=True)
    last_used_in_search: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))

    openai_file_id: Optional[str] = Field(default=None, index=True)
    openai_vector_store_id: Optional[str] = Field(default=None, index=True)

    error_code: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))

    user: AppUser = Relationship(back_populates="documents")
    conversations: List["ConversationDocument"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    provider_artifacts: List["DocumentProviderArtifact"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class ConversationDocument(SQLModel, table=True):
    __tablename__ = "conversation_document"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(foreign_key="conversation.id", index=True)
    document_id: uuid.UUID = Field(foreign_key="user_document.id", index=True)
    attached_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))

    conversation: Conversation = Relationship(back_populates="attached_documents")
    document: UserDocument = Relationship(back_populates="conversations")

    __table_args__ = (
        UniqueConstraint("conversation_id", "document_id", name="uq_conversation_document"),
    )


class ConversationSearchChunk(SQLModel, table=True):
    __tablename__ = "conversation_search_chunk"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    conversation_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    message_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("message.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    message_content_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("messagecontent.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    message_role: str = Field(index=True)
    chunk_ordinal: int = Field(default=0)
    chunk_text: str
    text_hash: str = Field(index=True)
    embedding: list[float] = Field(default_factory=list, sa_column=Column(ARRAY(Float), nullable=False))
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    __table_args__ = (
        UniqueConstraint(
            "message_content_id",
            "chunk_ordinal",
            name="uq_conversation_search_chunk_message_content_chunk",
        ),
        Index("ix_conversation_search_chunk_user_conversation", "user_id", "conversation_id"),
    )


class ConversationSearchProjection(SQLModel, table=True):
    __tablename__ = "conversation_search_projection"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    conversation_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    projection_text: str
    summary_source: str = Field(default="recent_visible_transcript")
    embedding: list[float] = Field(default_factory=list, sa_column=Column(ARRAY(Float), nullable=False))
    last_indexed_message_id: Optional[uuid.UUID] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_conversation_search_projection_conversation"),
        Index("ix_conversation_search_projection_user_conversation", "user_id", "conversation_id"),
    )


class ConversationSearchJob(SQLModel, table=True):
    __tablename__ = "conversation_search_job"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_type: str = Field(index=True)
    conversation_id: uuid.UUID = Field(
        sa_column=Column(ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    message_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(ForeignKey("message.id", ondelete="CASCADE"), nullable=True, index=True),
    )
    status: str = Field(default="pending", index=True)
    dedupe_key: str = Field(index=True)
    attempt_count: int = Field(default=0)
    run_after: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    locked_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, nullable=True, index=True))
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    __table_args__ = (
        Index("ix_conversation_search_job_status_run_after", "status", "run_after"),
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
    payment_method_id: Optional[uuid.UUID] = Field(default=None, foreign_key="payment_methods.id", index=True)
    flow_kind: str = Field(default="purchase", index=True)
    renewal_failure_reason: Optional[str] = Field(default=None, index=True)
    bound_method_snapshot: Optional[dict] = Field(default=None, sa_column=Column(JSONB, nullable=True))

    created_at: datetime = Field(default_factory=utcnow_naive)
    updated_at: datetime = Field(default_factory=utcnow_naive)

    user: "AppUser" = Relationship()
    payment_method: Optional["PaymentMethod"] = Relationship()


class PaymentMethodType(str, Enum):
    card = "card"
    sbp = "sbp"


class PaymentMethodStatus(str, Enum):
    pending = "pending"
    active = "active"
    detached = "detached"
    failed = "failed"


class BindingMethodType(str, Enum):
    auto = "auto"
    card = "card"
    sbp = "sbp"


class BindingSessionStatus(str, Enum):
    pending = "pending"
    active = "active"
    failed = "failed"
    cancelled = "cancelled"


class DocumentProvider(str, Enum):
    openai = "openai"
    google = "google"


class DocumentProviderArtifactStatus(str, Enum):
    uploading = "uploading"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    delete_queued = "delete_queued"
    deleted = "deleted"


class PaymentMethod(SQLModel, table=True):
    __tablename__ = "payment_methods"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)

    # Existing card token
    rebill_id: Optional[str] = Field(default=None, index=True)

    # New SBP token
    account_token: Optional[str] = Field(default=None, index=True)

    # Type discriminator
    type: str = Field(default="card", index=True)

    # Card info for UI (e.g., "Visa •••• 4242")
    card_type: str = Field(default="Unknown")
    pan: str = Field(default="****")
    exp_date: str = Field(default="")  # MMYY

    # Phone for SBP if available
    phone: Optional[str] = Field(default=None)

    status: str = Field(default=PaymentMethodStatus.active.value, index=True)
    is_default: bool = Field(default=False)
    bound_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    detached_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    last_charge_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    last_charge_status: Optional[str] = Field(default=None)
    last_charge_error: Optional[str] = Field(default=None)
    binding_request_key: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow_naive)

    user: "AppUser" = Relationship()


class PaymentBindingSession(SQLModel, table=True):
    __tablename__ = "payment_binding_session"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    tier_id: Optional[uuid.UUID] = Field(default=None, foreign_key="subscription_tier.id", index=True)
    method_type: str = Field(default=BindingMethodType.auto.value, index=True)
    status: str = Field(default=BindingSessionStatus.pending.value, index=True)
    request_key: str = Field(index=True, unique=True)
    payment_url: Optional[str] = Field(default=None)
    qr_payload: Optional[str] = Field(default=None)
    qr_image_svg: Optional[str] = Field(default=None)
    bank_member_id: Optional[str] = Field(default=None)
    linked_payment_method_id: Optional[uuid.UUID] = Field(default=None, foreign_key="payment_methods.id", index=True)
    error_code: Optional[str] = Field(default=None, index=True)
    error_message: Optional[str] = Field(default=None)
    bound_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    user: "AppUser" = Relationship(back_populates="payment_binding_sessions")
    linked_payment_method: Optional["PaymentMethod"] = Relationship()


class DocumentProviderArtifact(SQLModel, table=True):
    __tablename__ = "document_provider_artifact"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="user_document.id", index=True)
    provider: str = Field(default=DocumentProvider.openai.value, index=True)
    status: str = Field(default=DocumentProviderArtifactStatus.uploading.value, index=True)
    external_file_id: Optional[str] = Field(default=None, index=True)
    external_index_id: Optional[str] = Field(default=None, index=True)
    error_code: Optional[str] = Field(default=None, index=True)
    error_message: Optional[str] = Field(default=None)
    indexed_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime, index=True))
    created_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, index=True))
    updated_at: datetime = Field(default_factory=utcnow_naive, sa_column=Column(DateTime, onupdate=utcnow_naive))

    document: UserDocument = Relationship(back_populates="provider_artifacts")

    __table_args__ = (
        UniqueConstraint("document_id", "provider", name="uq_document_provider_artifact"),
    )


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
    description_ru: Optional[str] = None
    is_active: bool = Field(default=True)
