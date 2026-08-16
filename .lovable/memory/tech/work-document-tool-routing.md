---
name: Work document tool routing
description: Preserve all Work sources when attached documents exceed the OpenAI file-search vector-store limit.
type: tech
---

## Context

Each uploaded document currently receives its own OpenAI vector store. Agentic
Work accepts up to five documents, while one Responses API file-search tool
accepts at most two vector stores.

## Decision

Use file search when the selected documents resolve to at most two distinct
vector stores. When there are more, omit file search and stage every selected
document in the existing Code Interpreter container. Never truncate the vector
store list because that would silently exclude user sources.

## Future changes

- Include the Code Interpreter container cost in the reservation before calling
  the provider.
- Keep every document available in `searchable_source_files` and evidence mapping.
- Cover both the two-store path and a three-or-more-store fallback in tests.
- If indexing is redesigned, prefer one bounded store per Work scope and retain
  cleanup, idempotency, and cost accounting.
