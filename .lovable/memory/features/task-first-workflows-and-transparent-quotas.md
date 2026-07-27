---
name: Task-first workflows and transparent request quotas
description: Product contract for task presets, completed-answer accounting, workflow attribution, and cross-surface chat links.
type: feature
---

## Context

AIwithUI differentiates from token and credit wallets through understandable request pools. The interface should start from the user's job, while keeping model controls available for advanced users.

## Decision

- Home exposes task presets instead of leading with providers or model names.
- A preset may recommend a model only when its entitlement pool is available; it must never bypass backend gating.
- Every text send may include `workflow_kind`; the backend stores it on the immutable request ledger.
- The public text contract is: one completed text answer uses one request. Failed generations are refunded. Images remain a separate energy system.
- Before sending, show the selected shared pool and remaining requests or unlimited state.
- Conversation URLs use the optional `chat` query parameter, and Markdown export is available from the web header.
- Projects are the product name for the existing folder primitive. They persist shared instructions and selected ready documents; a new project chat inherits those documents.
- Public pricing and capability surfaces should consume `/api/v1/public/catalog` rather than copy database facts.

## Constraints and gotchas

- Do not describe image generation as one-request accounting.
- Do not advertise usage packs unless public pack rows and checkout are enabled in production.
- Workflow presets are guidance, not a new entitlement source.
- Preserve the existing `client_request_id` idempotency and SSE resume behavior.
- Cache-aware cost telemetry must keep unknown cache rates conservative by falling back to uncached input pricing.
