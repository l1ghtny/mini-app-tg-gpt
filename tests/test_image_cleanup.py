from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.image_cleanup import (
    BucketObject,
    orphan_policy_allows,
    select_cleanup_candidates,
)


NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _object(key: str, age: timedelta) -> BucketObject:
    return BucketObject(
        key=key,
        size=100,
        etag="etag",
        last_modified=NOW - age,
    )


def _asset(*, conversation_id=None, message_content_id=None):
    return SimpleNamespace(
        conversation_id=conversation_id,
        message_content_id=message_content_id,
    )


def test_orphan_policy_only_accepts_managed_expired_prefixes():
    assert orphan_policy_allows(
        "images/partial/aa/image.png",
        last_modified=NOW - timedelta(days=2),
        now=NOW,
        partial_days=1,
        free_days=30,
        paid_days=365,
    )
    assert orphan_policy_allows(
        "images/free/uploaded/aa/image.png",
        last_modified=NOW - timedelta(days=31),
        now=NOW,
        partial_days=1,
        free_days=30,
        paid_days=365,
    )
    assert not orphan_policy_allows(
        "images/paid/uploaded/aa/image.png",
        last_modified=NOW - timedelta(days=364),
        now=NOW,
        partial_days=1,
        free_days=30,
        paid_days=365,
    )
    assert not orphan_policy_allows(
        "tg-bot-images/2026/05/image.png",
        last_modified=NOW - timedelta(days=400),
        now=NOW,
        partial_days=1,
        free_days=30,
        paid_days=365,
    )


def test_candidate_selection_preserves_live_shared_and_legacy_objects():
    detached_key = "images/free/uploaded/2026/01/detached.png"
    live_key = "images/free/generated/aa/live.png"
    message_key = "images/free/generated/aa/message.png"
    derived_key = "images/free/uploaded/2026/01/derived.png"
    orphan_key = "images/free/uploaded/2026/01/orphan.png"
    legacy_key = "tg-bot-images/2026/01/legacy.png"
    objects = [
        _object(detached_key, timedelta(days=3)),
        _object(live_key, timedelta(days=40)),
        _object(message_key, timedelta(days=40)),
        _object(derived_key, timedelta(days=40)),
        _object(orphan_key, timedelta(days=40)),
        _object(legacy_key, timedelta(days=400)),
    ]
    assets_by_key = {
        detached_key: [_asset()],
        live_key: [_asset(conversation_id="conversation")],
    }

    candidates = select_cleanup_candidates(
        objects,
        assets_by_key=assets_by_key,
        message_keys={message_key},
        derived_keys={derived_key},
        now=NOW,
        detached_grace_hours=48,
        partial_days=1,
        free_days=30,
        paid_days=365,
    )

    assert [(item.object.key, item.reason) for item in candidates] == [
        (detached_key, "detached_asset"),
        (orphan_key, "orphan"),
    ]
