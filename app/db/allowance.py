"""Accounting is independent of mutable conversations and old reply counters."""

import uuid
from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    UniqueConstraint,
    CheckConstraint,
    JSON,
)
from sqlmodel import SQLModel, Field
from app.db.models import utcnow_naive


class AllowanceAccount(SQLModel, table=True):
    __tablename__ = "allowance_account"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    scope: str = Field(index=True)
    period_start: datetime = Field(sa_column=Column(DateTime, nullable=False))
    period_end: datetime = Field(sa_column=Column(DateTime, nullable=False))
    plan: str
    rate_version: str
    granted: int = Field(sa_column=Column(BigInteger, nullable=False))
    spent: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    reserved: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    luna_granted: int = Field(sa_column=Column(BigInteger, nullable=False))
    luna_spent: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    luna_reserved: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    __table_args__ = (
        UniqueConstraint(
            "user_id", "scope", "period_start", name="uq_allowance_period"
        ),
        CheckConstraint(
            "granted >= 0 AND spent >= 0 AND reserved >= 0 AND spent + reserved <= granted",
            name="ck_allowance_balance",
        ),
        CheckConstraint(
            "luna_granted >= 0 AND luna_spent >= 0 AND luna_reserved >= 0 AND luna_spent + luna_reserved <= luna_granted",
            name="ck_allowance_luna",
        ),
    )


class AllowanceRequest(SQLModel, table=True):
    __tablename__ = "allowance_request"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    account_id: uuid.UUID = Field(foreign_key="allowance_account.id", index=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    scope: str
    request_id: str
    conversation_id: uuid.UUID | None = Field(default=None)
    model: str
    ceiling: int = Field(sa_column=Column(BigInteger, nullable=False))
    charged: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    luna_ceiling: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    luna_charged: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    status: str = "reserved"
    created_at: datetime = Field(default_factory=utcnow_naive)
    __table_args__ = (
        UniqueConstraint("user_id", "scope", "request_id", name="uq_allowance_request"),
    )


class AllowanceEvent(SQLModel, table=True):
    __tablename__ = "allowance_event"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    account_id: uuid.UUID = Field(foreign_key="allowance_account.id", index=True)
    request_id: uuid.UUID | None = Field(
        default=None, foreign_key="allowance_request.id"
    )
    event_key: str = Field(unique=True)
    kind: str
    units: int = Field(sa_column=Column(BigInteger, nullable=False))
    luna_units: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    created_at: datetime = Field(default_factory=utcnow_naive)


class ProviderAttempt(SQLModel, table=True):
    __tablename__ = "allowance_provider_attempt"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    request_id: uuid.UUID = Field(foreign_key="allowance_request.id", index=True)
    step_key: str
    model: str
    provider_id: str | None = None
    status: str = "started"
    budget: int = Field(sa_column=Column(BigInteger, nullable=False))
    supplier_units: int | None = Field(default=None, sa_column=Column(BigInteger))
    customer_units: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    included: bool = False
    input_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    search_calls: int = 0
    file_calls: int = 0
    usage_details: dict | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow_naive)
    __table_args__ = (
        UniqueConstraint("request_id", "step_key", name="uq_allowance_attempt"),
    )
