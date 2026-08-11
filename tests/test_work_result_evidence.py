from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.work_runs.evidence import (
    attach_legacy_source_links,
    build_work_evidence,
)


def test_provider_annotations_become_deduplicated_structured_evidence() -> None:
    document_id = uuid.uuid4()
    document = SimpleNamespace(
        id=document_id,
        filename="launch-brief.pdf",
        openai_file_id="file-private-provider-id",
    )
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(
                        annotations=[
                            SimpleNamespace(
                                type="url_citation",
                                title="Primary research",
                                url="https://example.com/research",
                                start_index=35,
                                end_index=38,
                            ),
                            SimpleNamespace(
                                type="url_citation",
                                title="Primary research",
                                url="https://example.com/research",
                                start_index=82,
                                end_index=85,
                            ),
                            SimpleNamespace(
                                type="file_citation",
                                filename="provider-name.pdf",
                                file_id="file-private-provider-id",
                                index=0,
                            ),
                        ]
                    )
                ]
            )
        ]
    )

    evidence = build_work_evidence(response, documents=[document])

    assert evidence == {
        "version": 1,
        "sources": [
            {
                "id": "source-1",
                "type": "web",
                "title": "Primary research",
                "url": "https://example.com/research",
            },
            {
                "id": "source-2",
                "type": "document",
                "title": "launch-brief.pdf",
                "filename": "launch-brief.pdf",
                "document_id": str(document_id),
            },
        ],
        "citations": [
            {"source_id": "source-1", "start_index": 35, "end_index": 38},
            {"source_id": "source-1", "start_index": 82, "end_index": 85},
            {"source_id": "source-2"},
        ],
    }
    assert "file-private-provider-id" not in str(evidence)


def test_nested_annotations_and_unsafe_urls_are_handled_defensively() -> None:
    response = {
        "output": [
            {
                "content": [
                    {
                        "annotations": [
                            {
                                "url_citation": {
                                    "type": "url_citation",
                                    "title": "[Useful source]",
                                    "url": "https://example.com/useful",
                                }
                            },
                            {
                                "type": "url_citation",
                                "title": "Unsafe source",
                                "url": "javascript:alert(1)",
                            },
                        ]
                    }
                ]
            }
        ]
    }

    evidence = build_work_evidence(response)

    assert evidence["sources"] == [
        {
            "id": "source-1",
            "type": "web",
            "title": "Useful source",
            "url": "https://example.com/useful",
        }
    ]
    assert evidence["citations"] == [{"source_id": "source-1"}]


def test_legacy_markdown_only_adds_web_sources_missing_from_the_result() -> None:
    evidence = {
        "version": 1,
        "sources": [
            {
                "id": "source-1",
                "type": "web",
                "title": "Example",
                "url": "https://example.com/source",
            },
            {
                "id": "source-2",
                "type": "document",
                "title": "brief.pdf",
                "filename": "brief.pdf",
            },
        ],
        "citations": [],
    }

    result = attach_legacy_source_links("Useful result.", evidence)

    assert result == (
        "Useful result.\n\n### Sources\n"
        "- [Example](https://example.com/source)"
    )
    assert attach_legacy_source_links(result, evidence) == result
