import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from app.api.chat_helpers import handle_update_conversation_settings
from app.db.models import Conversation
from app.schemas.chat import UpdateConversationSettingsRequest


@pytest.mark.asyncio
@pytest.mark.parametrize('choice', ['auto', [], ['web_search'], 'web_search'])
async def test_tool_choice_is_saved_without_losing_scalar_list_distinction(choice):
    user = SimpleNamespace(id=uuid.uuid4())
    conversation = Conversation(user_id=user.id, model='gpt-5.4-nano', image_model='gpt-image-1.5')
    session = SimpleNamespace(get=AsyncMock(return_value=conversation), add=lambda _:None, commit=AsyncMock(), refresh=AsyncMock())
    result = await handle_update_conversation_settings(conversation_id=conversation.id, request=UpdateConversationSettingsRequest(tool_choice=choice), session=session, current_user=user)
    assert result.tool_choice == choice
    session.commit.assert_awaited_once()
    await handle_update_conversation_settings(conversation_id=conversation.id, request=UpdateConversationSettingsRequest(thinking=False), session=session, current_user=user)
    assert result.tool_choice == choice


@pytest.mark.asyncio
async def test_foreign_conversation_cannot_change_tools():
    session = SimpleNamespace(get=AsyncMock(return_value=Conversation(user_id=uuid.uuid4())), commit=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await handle_update_conversation_settings(conversation_id=uuid.uuid4(),request=UpdateConversationSettingsRequest(tool_choice=[]),session=session,current_user=SimpleNamespace(id=uuid.uuid4()))
    assert exc.value.status_code == 404
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_perplexity_settings_remain_unchanged():
    user = SimpleNamespace(id=uuid.uuid4())
    conversation = Conversation(user_id=user.id, model='sonar', tool_choice='auto')
    session = SimpleNamespace(get=AsyncMock(return_value=conversation),add=lambda _:None,commit=AsyncMock(),refresh=AsyncMock())
    await handle_update_conversation_settings(conversation_id=conversation.id,request=UpdateConversationSettingsRequest(tool_choice=[]),session=session,current_user=user)
    assert conversation.tool_choice == 'auto'
