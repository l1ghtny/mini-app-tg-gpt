from typing import List, Optional

from sqlalchemy import BigInteger, Column
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
    model: str = Field(default="gpt-4o-mini")
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

    type: str  # "text" or "image_url"
    value: str  # The actual text or the URL for the image

    message: Message = Relationship(back_populates="content")