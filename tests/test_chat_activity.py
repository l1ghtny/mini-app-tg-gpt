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
