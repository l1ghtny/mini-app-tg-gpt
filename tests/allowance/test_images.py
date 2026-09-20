from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from app.services import allowance_images as images


@pytest.mark.asyncio
async def test_unowned_reference_never_reaches_storage_or_provider(monkeypatch):
    for key in ("R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "unused-test-value")
    monkeypatch.setenv("R2_ENDPOINT", "https://storage.invalid")
    from app.services.background import image_deriver
    from app.r2 import methods

    guard = AsyncMock(side_effect=HTTPException(404, "Not found"))
    read = AsyncMock()
    monkeypatch.setattr(image_deriver, "require_owned_image_asset", guard)
    monkeypatch.setattr(methods, "get_bytes", read)
    with pytest.raises(HTTPException):
        await images.image_files(
            [{"image_url": "https://example.invalid/someone-elses.png"}],
            SimpleNamespace(user_id="owner"),
        )
    assert guard.call_args.kwargs["user_id"] == "owner"
    read.assert_not_called()


@pytest.mark.asyncio
async def test_new_image_does_not_reuse_previous_image(monkeypatch):
    from app.services import openai_service

    usage = {
        "input_tokens": 10,
        "input_tokens_details": {"text_tokens": 10, "image_tokens": 0},
        "output_tokens": 20,
    }
    response = SimpleNamespace(
        usage=SimpleNamespace(model_dump=lambda: usage),
        data=[SimpleNamespace(b64_json="synthetic-image")],
        _request_id="provider-request",
    )
    generate, edit = AsyncMock(return_value=response), AsyncMock()
    monkeypatch.setattr(
        openai_service,
        "client",
        SimpleNamespace(
            with_options=lambda **kw: SimpleNamespace(
                images=SimpleNamespace(generate=generate, edit=edit)
            )
        ),
    )
    references = AsyncMock()
    monkeypatch.setattr(images, "image_files", references)
    run = SimpleNamespace(
        start=AsyncMock(return_value="attempt"),
        finish=AsyncMock(),
        conversation_id="chat",
    )
    result = await images.generate_image(
        run, "A new unrelated landscape", [{"image_url": "old"}], "medium", "none"
    )
    assert result == ["synthetic-image"]
    references.assert_not_called()
    edit.assert_not_called()
    generate.assert_awaited_once()
    assert run.finish.call_args.kwargs["units"] == 650


@pytest.mark.asyncio
async def test_invalid_reference_mode_cannot_start_spend():
    run = SimpleNamespace(start=AsyncMock())
    with pytest.raises(ValueError):
        await images.generate_image(run, "edit", [], "medium", "any-account")
    run.start.assert_not_called()


@pytest.mark.asyncio
async def test_provider_rejection_releases_known_image_exposure(monkeypatch):
    import httpx
    from openai import APIStatusError
    from app.services import openai_service

    failure = APIStatusError(
        "rejected",
        response=httpx.Response(
            429,
            request=httpx.Request(
                "POST", "https://api.openai.com/v1/images/generations"
            ),
        ),
        body={},
    )
    generate = AsyncMock(side_effect=failure)
    monkeypatch.setattr(
        openai_service,
        "client",
        SimpleNamespace(
            with_options=lambda **kw: SimpleNamespace(
                images=SimpleNamespace(generate=generate)
            )
        ),
    )
    run = SimpleNamespace(
        start=AsyncMock(return_value="attempt"),
        finish=AsyncMock(),
        conversation_id=None,
    )
    with pytest.raises(APIStatusError):
        await images.generate_image(run, "A cup", [], "low", "none")
    assert run.start.call_args.args[1] < 20000
    assert run.finish.call_args.kwargs["units"] == 0
    assert run.finish.call_args.kwargs["success"] is False


@pytest.mark.asyncio
async def test_unknown_image_failure_is_not_falsely_recorded_as_free(monkeypatch):
    from app.services import openai_service

    generate = AsyncMock(side_effect=TimeoutError("provider connection lost"))
    monkeypatch.setattr(
        openai_service,
        "client",
        SimpleNamespace(
            with_options=lambda **kw: SimpleNamespace(
                images=SimpleNamespace(generate=generate)
            )
        ),
    )
    run = SimpleNamespace(
        start=AsyncMock(return_value="attempt"),
        finish=AsyncMock(),
        conversation_id=None,
    )
    with pytest.raises(TimeoutError):
        await images.generate_image(run, "A cup", [], "medium", "none")
    run.start.assert_awaited_once()
    run.finish.assert_not_called()


def test_image_usage_requires_complete_and_consistent_input_split():
    with pytest.raises(ValueError, match="omitted"):
        images.image_usage_units({"input_tokens": 50, "output_tokens": 196})
    with pytest.raises(ValueError, match="Invalid"):
        images.image_usage_units(
            {
                "input_tokens": 50,
                "output_tokens": 196,
                "input_tokens_details": {"text_tokens": 10, "image_tokens": 30},
            }
        )
