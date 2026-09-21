from unittest.mock import AsyncMock
import pytest
from app.services import allowance



@pytest.fixture
def document_helpers(monkeypatch):
    # Beta's existing Work document module imports storage configuration.
    # These capability tests must not require real storage credentials.
    for key, value in {
        "R2_BUCKET": "unused-test-bucket",
        "R2_ENDPOINT": "https://storage.invalid",
        "R2_ACCESS_KEY_ID": "test-only",
        "R2_SECRET_ACCESS_KEY": "test-only",
    }.items():
        monkeypatch.setenv(key, value)
    from app.api import document_helpers as helpers
    return helpers


def test_catalog_uses_navigation_groups_without_invented_scores():
    catalog = allowance.catalog()
    assert len(catalog["text_models"]) == 7
    for model in catalog["text_models"]:
        assert model["group"] in {"everyday", "standard", "advanced", "flagship"}
        assert model["intelligence"] is None
        assert model["description"] and model["description_ru"]
    assert catalog["image_models"][0]["description_ru"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan,docs,storage,pinned",
    [
        ("start", 50, 200, 25),
        ("plus", 100, 500, 50),
        ("premium", 200, 1024, 100),
        ("max", 200, 1024, 100),
    ],
)
async def test_document_capacity_follows_existing_shared_grant(
    db, monkeypatch, document_helpers, plan, docs, storage, pinned
):
    _, session, user = db
    monkeypatch.setattr(allowance.settings, "SHARED_ALLOWANCE_BETA_PLAN", plan)
    account = await allowance.account(session, user.id)
    await session.commit()
    # A changed rollout default must not relabel an existing monthly grant.
    monkeypatch.setattr(allowance.settings, "SHARED_ALLOWANCE_BETA_PLAN", "start")
    limits = await document_helpers._document_limits_for_user(session, user)
    assert limits.tier_name == account.plan.title()
    assert limits.max_active_docs == docs
    assert limits.max_storage_bytes == storage * 1024 * 1024
    assert limits.max_pinned_docs == pinned
    assert limits.doc_retention_hours == 120


@pytest.mark.asyncio
async def test_document_limits_leave_legacy_users_unchanged(db, monkeypatch, document_helpers):
    _, session, user = db
    monkeypatch.setattr(allowance.settings, "SHARED_ALLOWANCE_ENABLED", False)
    legacy = AsyncMock(return_value=None)
    monkeypatch.setattr(document_helpers, "get_active_tier", legacy)
    limits = await document_helpers._document_limits_for_user(session, user)
    legacy.assert_awaited_once_with(session, user.id)
    assert limits.max_active_docs == 2
    assert limits.max_pinned_docs == 0
