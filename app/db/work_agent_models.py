from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import uuid6
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.db.models import utcnow_naive


class WorkThread(SQLModel, table=True):
    __tablename__ = "work_thread"

    id: uuid.UUID = Field(default_factory=uuid6.uuid7, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", index=True)
    conversation_id: uuid.UUID | None = Field(default=None, index=True)
    folder_id: uuid.UUID | None = Field(default=None, index=True)
    title: str = Field(max_length=160)
    goal: str
    status: str = Field(default="planning", index=True, max_length=32)
    context_manifest: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
    updated_at: datetime = Field(
        default_factory=utcnow_naive,
        sa_column=Column(DateTime, onupdate=utcnow_naive),
    )

    __table_args__ = (
        Index("ix_work_thread_user_updated", "user_id", "updated_at"),
    )


class WorkThreadMessage(SQLModel, table=True):
    __tablename__ = "work_thread_message"

    id: uuid.UUID = Field(default_factory=uuid6.uuid7, primary_key=True)
    thread_id: uuid.UUID = Field(
        foreign_key="work_thread.id",
        index=True,
    )
    role: str = Field(max_length=16)
    kind: str = Field(default="message", max_length=32)
    content: str
    message_metadata: dict = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False),
    )
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)


class WorkPlan(SQLModel, table=True):
    __tablename__ = "work_plan"

    id: uuid.UUID = Field(default_factory=uuid6.uuid7, primary_key=True)
    thread_id: uuid.UUID = Field(foreign_key="work_thread.id", index=True)
    version: int = Field(default=1)
    status: str = Field(default="proposed", index=True, max_length=24)
    title: str = Field(max_length=160)
    summary: str
    execution_kind: str = Field(max_length=64)
    steps: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    expected_outputs: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False),
    )
    assumptions: list = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False),
    )
    provider: str | None = Field(default=None, max_length=32)
    model: str | None = Field(default=None, max_length=128)
    provider_response_id: str | None = Field(default=None, max_length=128)
    usage: dict = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    estimated_cost_usd: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 6), nullable=False),
    )
    actual_cost_usd: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 6), nullable=False),
    )
    approved_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)

    __table_args__ = (
        UniqueConstraint("thread_id", "version", name="uq_work_plan_thread_version"),
    )


class WorkThreadRun(SQLModel, table=True):
    __tablename__ = "work_thread_run"

    id: uuid.UUID = Field(default_factory=uuid6.uuid7, primary_key=True)
    thread_id: uuid.UUID = Field(foreign_key="work_thread.id", index=True)
    work_run_id: uuid.UUID = Field(foreign_key="work_run.id", unique=True, index=True)
    plan_id: uuid.UUID = Field(foreign_key="work_plan.id", index=True)
    ordinal: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow_naive)


class WorkHumanInputRequest(SQLModel, table=True):
    __tablename__ = "work_human_input_request"

    id: uuid.UUID = Field(default_factory=uuid6.uuid7, primary_key=True)
    thread_id: uuid.UUID = Field(foreign_key="work_thread.id", index=True)
    work_run_id: uuid.UUID = Field(foreign_key="work_run.id", index=True)
    round: int
    status: str = Field(default="pending", index=True, max_length=24)
    question: str
    reason: str | None = Field(default=None)
    answer: str | None = Field(default=None)
    provider: str = Field(max_length=32)
    provider_response_id: str = Field(max_length=128)
    provider_call_id: str = Field(max_length=128)
    answer_idempotency_key: str | None = Field(default=None, max_length=128)
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
    answered_at: datetime | None = Field(default=None)
    resumed_at: datetime | None = Field(default=None)

    __table_args__ = (
        CheckConstraint(
            "round >= 1 AND round <= 2",
            name="ck_work_human_input_round",
        ),
        CheckConstraint(
            "status IN ('pending', 'answered', 'resumed', 'cancelled')",
            name="ck_work_human_input_status",
        ),
        UniqueConstraint(
            "work_run_id",
            "round",
            name="uq_work_human_input_run_round",
        ),
        UniqueConstraint(
            "provider",
            "provider_call_id",
            name="uq_work_human_input_provider_call",
        ),
        Index(
            "uq_work_human_input_pending_run",
            "work_run_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
