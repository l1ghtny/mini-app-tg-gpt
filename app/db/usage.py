import uuid
from datetime import datetime, UTC
from enum import Enum
from typing import Literal, Optional
from sqlalchemy import Column, UniqueConstraint, DateTime, CheckConstraint
from sqlmodel import SQLModel, Field


class State(str, Enum):
    reserved = "reserved"
    consumed = "consumed"
    refunded = "refunded"
    failed = "failed"


class RequestLedger(SQLModel, table=True):

    __tablename__ = "request_ledger"
    """
    One row per billable request (text generation) or per generated image.
    Survives conversation/message deletions.
    """
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    # nullable references for diagnostics; DO NOT FK so deletes don’t cascade
    conversation_id: Optional[uuid.UUID] = Field(default=None, index=True)
    assistant_message_id: Optional[uuid.UUID] = Field(default=None, index=True)

    request_id: str = Field(index=True)  # client- or server-generated; used for idempotency
    model_name: str = Field(index=True)
    feature: str = Field(index=True)

    state: State = Field(default=State.reserved, index=True)
    tool_choice: Optional[str] = None     # e.g., "auto" or "image_generation"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
                                 sa_column=Column(DateTime, index=True))

    __table_args__ = (UniqueConstraint("user_id","request_id", name="uq_user_reqid"),
        CheckConstraint(
            "feature IN ('text','image','doc','deepsearch','web_search')",
            name="ck_request_feature",
        ),
    )