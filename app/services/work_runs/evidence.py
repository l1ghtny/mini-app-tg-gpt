from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _value(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _annotation_value(annotation: Any, name: str) -> Any:
    value = _value(annotation, name)
    if value is not None:
        return value
    for nested_name in ("url_citation", "file_citation"):
        nested = _value(annotation, nested_name)
        nested_value = _value(nested, name) if nested is not None else None
        if nested_value is not None:
            return nested_value
    return None


def _annotations(response: Any) -> Iterable[Any]:
    for item in _value(response, "output") or []:
        for content in _value(item, "content") or []:
            yield from _value(content, "annotations") or []


def _clean_label(value: Any, fallback: str) -> str:
    label = value.strip() if isinstance(value, str) else ""
    label = label.replace("[", "").replace("]", "")
    return (label or fallback)[:500]


def _valid_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.startswith(("https://", "http://")) else None


def _position(annotation: Any) -> tuple[int, int] | None:
    start = _annotation_value(annotation, "start_index")
    end = _annotation_value(annotation, "end_index")
    if (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and start >= 0
        and end > start
    ):
        return start, end
    return None


def build_work_evidence(
    response: Any,
    *,
    documents: Sequence[Any] = (),
    provider_file_document_ids: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Normalize provider annotations into a durable, provider-neutral contract."""

    provider_file_document_ids = provider_file_document_ids or {}
    documents_by_file_id = {
        file_id: document
        for document in documents
        if isinstance((file_id := getattr(document, "openai_file_id", None)), str)
        and file_id
    }
    documents_by_id = {str(document.id): document for document in documents}
    filenames: dict[str, list[Any]] = {}
    for document in documents:
        filenames.setdefault(document.filename, []).append(document)

    sources: list[dict[str, object]] = []
    citations: list[dict[str, object]] = []
    source_ids: dict[tuple[str, str], str] = {}
    seen_citations: set[tuple[str, int | None, int | None]] = set()

    def source_id(identity: tuple[str, str], payload: dict[str, object]) -> str:
        existing = source_ids.get(identity)
        if existing is not None:
            return existing
        created = f"source-{len(sources) + 1}"
        source_ids[identity] = created
        sources.append({"id": created, **payload})
        return created

    for annotation in _annotations(response):
        annotation_type = _annotation_value(annotation, "type")
        url = _valid_http_url(_annotation_value(annotation, "url"))
        if annotation_type == "url_citation" or url is not None:
            if url is None:
                continue
            current_source_id = source_id(
                ("web", url),
                {
                    "type": "web",
                    "title": _clean_label(
                        _annotation_value(annotation, "title"), url
                    ),
                    "url": url,
                },
            )
        elif annotation_type == "file_citation":
            file_id = _annotation_value(annotation, "file_id")
            filename_value = _annotation_value(annotation, "filename")
            filename = filename_value.strip() if isinstance(filename_value, str) else ""
            document = documents_by_file_id.get(file_id)
            if document is None and isinstance(file_id, str):
                document_id = provider_file_document_ids.get(file_id)
                document = documents_by_id.get(document_id) if document_id else None
            if document is None and filename and len(filenames.get(filename, [])) == 1:
                document = filenames[filename][0]
            if document is not None:
                document_id = str(document.id)
                filename = document.filename
                identity = ("document", document_id)
            else:
                if not filename:
                    continue
                document_id = None
                identity = ("document_name", filename)
            payload: dict[str, object] = {
                "type": "document",
                "title": _clean_label(filename, "Source document"),
                "filename": filename,
            }
            if document_id is not None:
                payload["document_id"] = document_id
            current_source_id = source_id(identity, payload)
        else:
            continue

        position = _position(annotation)
        start, end = position if position is not None else (None, None)
        citation_key = (current_source_id, start, end)
        if citation_key in seen_citations:
            continue
        seen_citations.add(citation_key)
        citation: dict[str, object] = {"source_id": current_source_id}
        if position is not None:
            citation.update({"start_index": start, "end_index": end})
        citations.append(citation)

    return {"version": 1, "sources": sources, "citations": citations}


def attach_legacy_source_links(content: str, evidence: Mapping[str, object]) -> str:
    """Keep sources visible to older frontends during a rolling deployment."""

    source_lines: list[str] = []
    for source in evidence.get("sources", []):
        if not isinstance(source, Mapping) or source.get("type") != "web":
            continue
        url = source.get("url")
        title = source.get("title")
        if not isinstance(url, str) or url in content:
            continue
        safe_title = _clean_label(title, url)
        source_lines.append(f"- [{safe_title}]({url})")
    if not source_lines:
        return content
    return f"{content.rstrip()}\n\n### Sources\n" + "\n".join(source_lines)
