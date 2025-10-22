import json
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.api.dependencies import get_current_user
from app.db import models
from app.db.database import get_session
from app.db.models import AppUser
from app.schemas.chat import Conversation, ConversationWithMessages, NewMessageRequest, RenameRequest, \
    UpdateConversationSettingsRequest
from app.services.openai_service import get_openai_response as get_openai_response
from app.services.tasks import generate_and_save_title

router = APIRouter()


@router.post("/conversations/{conversation_id}/messages")
async def chat_with_conversation(
        conversation_id: uuid.UUID,
        request: NewMessageRequest,
        background_tasks: BackgroundTasks,
        session: Session = Depends(get_session),
        current_user: AppUser = Depends(get_current_user)
):
    conversation = session.get(models.Conversation, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Checking if this is the first message in chat
    is_first_message = len(conversation.messages) == 0 and conversation.title == "New Chat"

    # 1. Create the parent Message object
    user_message = models.Message(conversation_id=conversation_id, role="user")
    session.add(user_message)
    session.commit()
    session.refresh(user_message)

    # --- SIMPLIFIED LOGIC ---
    # 2. Create MessageContent objects directly from the request
    for part in request.content:
        message_content = models.MessageContent(
            message_id=user_message.id,
            type=part.type,
            value=part.value
        )
        session.add(message_content)


    if conversation.model != request.model:
        conversation.model = request.model
        session.add(conversation)


    if request.system_prompt is not None and conversation.system_prompt != request.system_prompt:
        conversation.system_prompt = request.system_prompt
        session.add(conversation)
    session.commit()
    # -------------------------

    # Creating the task to generate the title
    if is_first_message:
        print('GENERATING TITLE')
        first_user_message_content = ""
        for part in request.content:
            if part.type == 'text':
                first_user_message_content = part.value
                break  # We only need the first text part for the title

        if first_user_message_content:
            background_tasks.add_task(generate_and_save_title, conversation_id, first_user_message_content)

    # 3. Prepare data for OpenAI (this part now also becomes simpler)
    history_for_openai = []
    for msg in conversation.messages:
        content_list = []
        for part in msg.content:
            if part.type == 'text' and msg.role == 'user':
                content_list.append({"type": "input_text", "text": part.value})
            elif part.type == 'text' and msg.role == 'assistant':
                content_list.append({"type": "output_text", "text": part.value})
            elif part.type == 'image_url':
                content_list.append({"type": "input_image", "image_url": part.value})
        history_for_openai.append({"role": msg.role, "content": content_list})

    async def stream_and_save():
        # ... (streaming logic remains the same, but saving is different)
        response_generator = get_openai_response(history_for_openai, model=request.model, tool_choice=request.tool_choice)

        # Create the parent assistant message first
        assistant_message = models.Message(conversation_id=conversation_id, role="assistant")
        session.add(assistant_message)
        session.commit()
        session.refresh(assistant_message)

        # Variables to accumulate content before saving
        current_text_chunk = ""

        async for chunk_json in response_generator:
            event = json.loads(chunk_json)

            if event['type'] == 'text_chunk':
                current_text_chunk += event['data']
            elif event['type'] == 'image':
                # Save the image part immediately
                image_content = models.MessageContent(
                    message_id=assistant_message.id, type="image_url", value=event['data']
                )
                session.add(image_content)

            yield chunk_json

        # 4. Save the final accumulated text chunk
        if current_text_chunk:
            text_content = models.MessageContent(
                message_id=assistant_message.id, type="text", value=current_text_chunk
            )
            session.add(text_content)

        session.commit()

    return StreamingResponse(stream_and_save(), media_type="text/event-stream")


@router.post("/conversations", response_model=Conversation)
def create_conversation(session: Session = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    """
    Creates a new conversation for the user.
    """
    # First, ensure the temporary user exists
    user = session.get(models.AppUser, current_user.id)
    if not user:
        user = models.AppUser(id=current_user.id, telegram_id=current_user.telegram_id)  # Example telegram_id
        session.add(user)
        session.commit()
        session.refresh(user)

    new_conversation = models.Conversation(title="New Chat", user_id=user.id)
    session.add(new_conversation)
    session.commit()
    session.refresh(new_conversation)
    return new_conversation


@router.get("/conversations", response_model=List[Conversation])
def get_conversations(session: Session = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    """
    Gets all conversations for the user.
    """
    conversations = session.exec(
        select(models.Conversation).where(models.Conversation.user_id == current_user.id)
    ).all()
    return conversations


@router.get("/conversations/{conversation_id}", response_model=ConversationWithMessages)
def get_conversation_messages(conversation_id: uuid.UUID, session: Session = Depends(get_session), current_user: AppUser = Depends(get_current_user)):
    """
    Gets a specific conversation and all its messages.
    """
    conversation = session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.put("/conversations/{conversation_id}", response_model=Conversation)
def rename_conversation(
    conversation_id: uuid.UUID,
    request: RenameRequest,
    session: Session = Depends(get_session),
    current_user: AppUser = Depends(get_current_user)
):
    """
    Renames a specific conversation.
    """
    conversation = session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.title = request.title
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: AppUser = Depends(get_current_user)
):
    """
    Deletes a specific conversation and all its messages.
    """
    conversation = session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # SQLModel will handle cascading deletes for messages if the relationship is set up correctly
    session.delete(conversation)
    session.commit()
    return


@router.put("/conversations/{conversation_id}/settings", response_model=Conversation)
def update_conversation_settings(
        conversation_id: uuid.UUID,
        request: UpdateConversationSettingsRequest,
        session: Session = Depends(get_session),
        current_user: models.AppUser = Depends(get_current_user)  # Secure the endpoint
):
    """
    Updates the settings (like system prompt) for a specific conversation.
    """
    conversation = session.get(models.Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Update fields if they were provided in the request
    if request.system_prompt is not None:
        conversation.system_prompt = request.system_prompt

    if request.model is not None:
        conversation.model = request.model

    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation