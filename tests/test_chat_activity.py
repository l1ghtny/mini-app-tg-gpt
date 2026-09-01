import uuid
from types import SimpleNamespace

import pytest

import app.services.chat_activity as chat_activity
from app.services.chat_activity import _merge_detail, _safe_sources


def test_safe_sources_keeps_public_web_metadata_only():
    sources = _safe_sources(
        [
            {"url": "https://www.example.com/report", "title": "Annual report", "snippet": "ignored"},
            {"url": "javascript:alert(1)", "title": "unsafe"},
            {"url": "https://token:secret@example.net/private", "title": "credentials"},
            "https://news.example.org/story",
            "https://www.example.com/report",
        ]
    )

    assert sources == [
        {
            "url": "https://www.example.com/report",
            "domain": "example.com",
            "title": "Annual report",
        },
        {
            "url": "https://news.example.org/story",
            "domain": "news.example.org",
        },
    ]


def test_activity_detail_merges_sources_without_duplicates():
    merged = _merge_detail(
        {"sources": [{"url": "https://a.example/one"}]},
        {
            "action": "open_page",
            "sources": [
                {"url": "https://a.example/one", "title": "One"},
                {"url": "https://b.example/two"},
            ],
        },
    )

    assert merged["action"] == "open_page"
    assert merged["sources"] == [
        {"url": "https://a.example/one", "domain": "a.example", "title": "One"},
        {"url": "https://b.example/two", "domain": "b.example"},
    ]


@pytest.mark.asyncio
async def test_commentary_status_updates_the_transient_turn_label(monkeypatch):
    captured: dict = {}

    async def fake_upsert(_session, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id=uuid.uuid4(),
            sequence=1,
            event_key=kwargs["event_key"],
            kind=kwargs["kind"],
            status=kwargs["status"],
            detail=kwargs["detail"],
            started_at=None,
            completed_at=None,
        )

    class FakeSession:
        async def commit(self):
            return None

    monkeypatch.setattr(chat_activity, "_upsert", fake_upsert, raising=True)

    events = await chat_activity.record_stream_activity(
        FakeSession(),
        message_id=uuid.uuid4(),
        event={
            "type": "status",
            "provider": "openai",
            "stage": "commentary",
            "status": "active",
            "label": "  Assessing   the setup  ",
        },
        lifecycle={},
    )

    assert captured["event_key"] == "turn"
    assert captured["detail"] == {
        "stage": "commentary",
        "label": "Assessing the setup",
        "provider": "openai",
    }
    assert events[0]["activity"]["detail"]["label"] == "Assessing the setup"
