---
name: Perplexity Sonar text provider
description: Perplexity Sonar is integrated as a text-only web-search provider with normalized SSE events.
type: tech
---

## Context

The app supports OpenAI and Google text providers. Perplexity was added to support live web-grounded answers without adding Grok/xAI.

## Decision

Perplexity models are exposed as text models:

- `sonar`
- `sonar-pro`

They use provider `perplexity`, stream through `app/services/perplexity_service.py`, and emit the same normalized SSE events as the existing providers.

Default AI search uses the OpenAI-compatible Sonar chat-completions path. Per-request `search_mode` maps to Perplexity search context size:

- `quick` -> `low`
- `standard` -> `medium`
- `deep` -> `high`

Explicit `fetch_url` and `finance_search` tool choices use Perplexity Agent API instead of chat completions. Agent responses are normalized into the same SSE stream shape and source links are appended to the assistant text. Agent API exact usage costs are stored in the existing token usage cost columns when Perplexity returns `usage.cost`.

Perplexity has no native image model in this app. Conversation settings keep an OpenAI image fallback (`gpt-image-1.5`) when a Perplexity text model is selected, but Perplexity requests reject:

- image input
- image generation
- document file search

## How To Apply

When adding frontend support, show Perplexity as an AI Search provider/model, not as a general multimodal model. Disable image generation, uploaded image prompts, and file-search controls while `provider === "perplexity"` or selected model is `sonar`/`sonar-pro`.

Frontend can send these Perplexity-only controls on `POST /api/v1/conversations/{id}/messages`:

- `search_mode`: `quick`, `standard`, or `deep`
- `tool_choice`: `fetch_url`
- `tool_choice`: `finance_search`
- optional combined list form, for example `["web_search", "fetch_url"]`

Do not show `fetch_url` or `finance_search` for OpenAI/Google models unless backend validation changes.

## Constraints

Runtime requires `PERPLEXITY_API_KEY`. `PERPLEXITY_SEARCH_CONTEXT_SIZE` defaults to `low` to control request fees.
