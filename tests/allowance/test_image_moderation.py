from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import BadRequestError

from app.services import allowance_images, generation_observability
from app.services.allowance_policy import FLARE
from app.services.provider_errors import ImageModerationError


def rejection(stage, *, nested=False, code='moderation_blocked'):
    body = {'code': code, 'message': 'PRIVATE PROVIDER PAYLOAD',
            'moderation_details': {'moderation_stage': stage, 'categories': ['PRIVATE']}}
    return BadRequestError('PRIVATE PROVIDER PAYLOAD', body={'error': body} if nested else body,
                           response=httpx.Response(400, request=httpx.Request('POST', 'https://api.openai.com/v1/images/edits')))


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['input', 'output'])
@pytest.mark.parametrize('edit', [False, True])
async def test_image_rejection_releases_attempt_and_preserves_only_safe_metadata(monkeypatch, stage, edit):
    from app.services import openai_service
    api = SimpleNamespace(edit=AsyncMock(side_effect=rejection(stage)), generate=AsyncMock(side_effect=rejection(stage, nested=True)))
    monkeypatch.setattr(openai_service, 'client', SimpleNamespace(with_options=lambda **kwargs: SimpleNamespace(images=api)))
    monkeypatch.setattr(allowance_images, 'image_files', AsyncMock(return_value=[('safe.png', b'image', 'image/png')]))
    monkeypatch.setattr(allowance_images, 'image_reference_tokens', lambda files: 100)
    run = SimpleNamespace(start=AsyncMock(return_value='attempt'), finish=AsyncMock(), conversation_id=None)
    with pytest.raises(ImageModerationError) as result:
        await allowance_images.generate_image(run, 'synthetic prompt', [{}] if edit else [], 'low', 'recent' if edit else 'none')
    assert result.value.stage == stage
    assert 'PRIVATE' not in str(result.value)
    run.finish.assert_awaited_once_with('attempt', FLARE, {
        'error_code': 'image_moderation_blocked', 'moderation_stage': stage,
        'image_action': 'edit' if edit else 'generate'}, success=False, units=0)
    assert api.edit.await_count + api.generate.await_count == 1  # No automatic retry of a safety block.


@pytest.mark.parametrize('stage', [None, {}, ['input'], 'PRIVATE'])
def test_untrusted_stage_is_not_exposed(stage):
    assert ImageModerationError.from_api_error(rejection(stage)).stage == 'unknown'


def test_other_bad_requests_are_not_mislabeled_as_safety_blocks():
    assert ImageModerationError.from_api_error(rejection('input', code='invalid_size')) is None
    assert ImageModerationError.from_api_error(SimpleNamespace(body='moderation_blocked')) is None


@pytest.mark.parametrize('stage', ['input', 'output', 'unknown'])
def test_sentry_rejection_keeps_alert_and_safe_classification(monkeypatch, stage):
    scope, capture = Mock(), Mock()
    @contextmanager
    def new_scope():
        yield scope
    monkeypatch.setattr(generation_observability.sentry_sdk, 'new_scope', new_scope)
    monkeypatch.setattr(generation_observability.sentry_sdk, 'capture_message', capture)
    lifecycle = {}
    for _ in range(2):
        generation_observability.report_generation_failure(lifecycle, model='claude-sonnet-5', request_id='test', exception=ImageModerationError(stage=stage))
    capture.assert_called_once()
    scope.set_tag.assert_any_call('chat.failure', 'true')
    scope.set_tag.assert_any_call('chat.error_code', 'image_moderation_blocked')
    scope.set_tag.assert_any_call('chat.failure_kind', 'provider_safety_rejection')
    scope.set_tag.assert_any_call('chat.moderation_stage', stage)
