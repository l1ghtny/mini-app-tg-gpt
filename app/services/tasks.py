import uuid

from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.database import get_session, engine
from app.services.openai_service import generate_conversation_title
from app.db import models

async def generate_and_save_title(conversation_id: uuid.UUID, first_message_content: str):
    print('CALLED THE TITLE FUNCTION')
    async with AsyncSession(engine, expire_on_commit=False) as session:
        new_title = await generate_conversation_title(first_message_content)
        conv_to_update = await session.get(models.Conversation, conversation_id)
        if conv_to_update and conv_to_update.title == "New Chat":
            conv_to_update.title = new_title
            session.add(conv_to_update)
            await session.commit()


