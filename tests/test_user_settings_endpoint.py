import os
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.dependencies import get_current_user
from app.api.user_settings import user_settings
from app.db.database import get_session
from app.db.models import AppUser


async def _create_user(session: AsyncSession, telegram_id: int) -> AppUser:
    user = AppUser(
        telegram_id=telegram_id,
        default_text_model="gpt-5.4-nano",
        default_image_model="gpt-image-1.5",
        default_thinking=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _build_app(engine, user: AppUser) -> FastAPI:
    app = FastAPI()
    app.include_router(user_settings, prefix="/api/v1")

    async def _fake_get_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session] = _fake_get_session
    return app


@pytest.mark.asyncio
async def test_user_settings_get_and_put():
    test_db_url = os.getenv("TEST_DATABASE_URL")
    assert test_db_url
    engine = create_async_engine(test_db_url, future=True, echo=False)

    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = await _create_user(session, 987654321)

    app = _build_app(engine, user)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Get current settings (should be defaults)
        response = await client.get("/api/v1/user/settings")
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_text_model"] == "gpt-5.4-nano"
        assert payload["default_image_model"] == "gpt-image-1.5"
        assert payload["default_thinking"] is True

        # 2. Put text model change only (should coerce image model to google default)
        response = await client.put(
            "/api/v1/user/settings",
            json={"default_text_model": "gemini-3.5-flash"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_text_model"] == "gemini-3.5-flash"
        assert payload["default_image_model"] == "gemini-2.5-flash-image"  # Coerced!

        # 3. Put mismatched models (should raise provider mismatch)
        response = await client.put(
            "/api/v1/user/settings",
            json={
                "default_text_model": "gpt-5.5",
                "default_image_model": "gemini-3-pro-image-preview"
            }
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["detail"]["error"] == "provider_mismatch"

        # 4. Put matching models (both OpenAI)
        response = await client.put(
            "/api/v1/user/settings",
            json={
                "default_text_model": "gpt-5.5",
                "default_image_model": "gpt-image-2"
            }
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_text_model"] == "gpt-5.5"
        assert payload["default_image_model"] == "gpt-image-2"

        # 5. Update default thinking and verify persistence
        response = await client.put(
            "/api/v1/user/settings",
            json={"default_thinking": False}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["default_thinking"] is False

    await engine.dispose()
