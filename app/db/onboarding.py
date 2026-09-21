"""Account-scoped visits for contextual onboarding; no message content stored."""
from datetime import datetime
from uuid import UUID
from sqlmodel import SQLModel, Field
from app.db.models import utcnow_naive


class OnboardingVisit(SQLModel, table=True):
    __tablename__ = "onboarding_visit"
    user_id: UUID = Field(foreign_key="app_user.id", primary_key=True)
    session_id: UUID = Field(primary_key=True)
    created_at: datetime = Field(default_factory=utcnow_naive, index=True)
