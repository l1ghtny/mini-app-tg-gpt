# AGENTS Guide (Backend Primary)

This file is the working guide for agents in the backend repo.

## Workspace Topology

- Backend repo (this root): [`G:\0\Coding_projects\Python\PycharmProjects\mini-app-tg-gpt`](G:/0/Coding_projects/Python/PycharmProjects/mini-app-tg-gpt)
- Frontend repo (reference only): [`G:\0\Coding_projects\frontend\gpt-mini-app-frontend-lovable`](G:/0/Coding_projects/frontend/gpt-mini-app-frontend-lovable)
- Shared project memory folder currently lives in the frontend repo: `.lovable/`

This backend repo should be usable independently. Treat the frontend repo as supporting context only.

## Ownership and Focus

- Backend work is the default priority in this repo.
- The frontend codebase is maintained by a dedicated frontend team.
- Use frontend files to understand contracts, payload normalization, SSE expectations, and rollout implications.
- Do not make opportunistic frontend edits from this repo unless explicitly requested.
- If a backend API/schema/streaming change requires frontend work, document the required frontend follow-up as a concrete change list.

## Repo Roles

### Backend (`mini-app-tg-gpt`)

- Stack: `FastAPI + SQLModel/SQLAlchemy + PostgreSQL + Redis + OpenAI Responses API`.
- Entrypoint: `main.py`; routers under `app/api/`.
- Chat pipeline:
  - message creation + entitlement checks + idempotency in `app/api/chat_helpers.py`
  - stream event production in `app/services/openai_service.py`
  - event bus transport in `app/redis/event_bus.py`
  - persistence/finalization in `app/api/helpers.py`
- Billing/limits model is ledger-driven (`RequestLedger`, subscriptions, usage packs, image energy pacing).
- OpenAI chaining state is explicit and invalidated on timeline rewrites (edit/delete/regenerate).
- Deployment uses Argo Rollouts canary manifests under `k8s/`.

### Frontend Reference (`gpt-mini-app-frontend-lovable`)

- Stack: `React 18 + TypeScript + Vite + Tailwind + shadcn/ui + Zustand + TanStack Query`.
- Auth UX is Telegram-first (`AuthGate`), with debug login path for local/test mode.
- API integration is centralized in `src/lib/api.ts` with strong normalization/back-compat logic.
- Streaming UX is SSE-based with reconnect/resume support:
  - parser: `src/lib/sseParser.ts`
  - transport: `src/lib/sseClient.ts`
  - recovery/resume: `src/lib/streamRecovery.ts`
- Error and product telemetry is integrated with Sentry.
- Deployment includes canary routing hooks (`src/lib/canary.ts`, `k8s/`, `scripts/release/`).

## Current Architectural Principles

1. **Defensive compatibility over brittle strictness**  
   Preserve backward-compatible payload handling across historical model aliases, tool choice variants, image quality aliases, and optional fields.

2. **Resilience in streaming paths**  
   SSE streams are lossy transport. Preserve reconnect, resume by `Last-Event-ID`, and reconciliation from server state.

3. **Idempotency for billable operations**  
   Message send flow requires `client_request_id`; backend deduplicates via `RequestLedger` unique keys and may return existing result links.

4. **Entitlement-first feature gating**  
   Model/tool/image/document behavior is constrained by active tier/packs and pacing checks before generation begins.

5. **Mutable UX, immutable accounting intent**  
   Conversation history can be truncated/edited; accounting and entitlement consumption remain explicit via ledger state transitions.

6. **Observability as a first-class concern**  
   Sentry and structured logging matter. Backend emits chain metrics; avoid changes that reduce failure visibility.

7. **Canary-safe rollout posture**  
   `X-Canary-User` routing exists end-to-end; preserve it when touching headers, ingress, or rollout-sensitive flows.

## Cross-Repo Contracts You Must Preserve

### Auth contract

- Frontend uses `/api/v1/auth/telegram` and `/api/v1/auth/debug-login`.
- Backend returns `access_token`; frontend normalizes it to `token`.
- Any auth payload change must be coordinated with `normalizeAuthResponse` in `src/lib/api.ts`.

### Conversation + message contract

- Primary flow:
  1. `POST /api/v1/conversations/{id}/messages` returns assistant message id + stream URL.
  2. Frontend opens SSE stream URL.
  3. Stream emits status/text/image/done/error events.
- Resume flow:
  - Frontend calls `GET /api/v1/conversations/{cid}/stream`.
  - Backend returns `307` redirect to active message stream or `204` when no active stream.

### Tool choice semantics

- Frontend intentionally sends `[]` to mean "no tools"; it does **not** send `"none"` as enum.
- If backend validation changes, frontend tool normalization must be updated as follow-up work.

### Timeline rewrite semantics

- Edit/delete/regenerate operations truncate chat history after a target point.
- These operations must keep invalidating chain state (`last_openai_response_id` + fingerprint metadata).

### Image/document semantics

- Image generation and document search are entitlement-gated.
- Image partial/final stream events must stay compatible with `sseParser.ts` mapping logic.

## Backend-First Change Policy

- Default to backend-only implementation in this repo.
- Use frontend source only to verify contracts and integration impact.
- If you change an existing backend API method, response shape, validation rule, stream event, or entitlement behavior:
  - implement the backend change
  - identify the exact frontend files likely affected
  - add a short frontend change list in your final handoff or state update
- If the frontend would break without same-session updates, say so explicitly rather than silently assuming the frontend team will catch it.

## Where To Change What

- Backend endpoint or schema change:
  - update backend schema/router/helper
  - inspect frontend `src/lib/api.ts` normalizers as reference
  - inspect frontend `src/types/index.ts` if the shape is mirrored there
  - inspect parser/recovery code if stream event types changed
  - document the frontend follow-up list if contract impact exists
- New model/tier/entitlement behavior:
  - backend DB models/migrations + entitlement services
  - note any frontend settings/catalog surfaces that need follow-up
- Stream event change:
  - backend emitter (`openai_service.py` / `helpers.py`)
  - note required frontend parser/client/recovery changes

## Development Commands

### Backend

- Install: `poetry install`
- Run API (common local): `poetry run fastapi dev main.py --host 0.0.0.0 --port 8080`
- Tests: `poetry run pytest`
- Migrations: `poetry run alembic upgrade head`

### Frontend Reference

- Install: `npm install`
- Dev server: `npm run dev`
- Tests: `npm run test`
- Build: `npm run build`

## Test Expectations

- Backend: prioritize `pytest` for any API/entitlement/streaming change.
- Frontend: only run vitest/build when the task explicitly includes frontend work or when contract validation requires it.
- For cross-repo API changes, validate one real end-to-end send/stream/resume flow when feasible.

## Env and Config Notes

- Backend settings are in `app/core/config.py` and related settings files (`redis`, `r2`).
- Frontend key envs are in the frontend repo `.env.example` (`VITE_API_URL`, Sentry vars, canary vars, debug vars).
- Do not hardcode secrets or environment-specific hostnames outside env-configured paths.

## Deployment Notes

- Backend canary artifacts:
  - `k8s/argo-rollouts/tg-mini-backend-rollout.yaml`
  - migration job template: `k8s/migrate-job.yaml.tpl`
- Frontend canary artifacts are reference-only and live in the frontend repo:
  - `k8s/argo-rollouts/tg-mini-frontend-rollout.yaml`
  - `scripts/release/deploy_canary.sh`, `promote_canary.sh`, `abort_canary.sh`

## Agent Quality Bar

- Keep changes narrowly scoped to requested behavior.
- Prefer existing backend patterns over new abstractions.
- Preserve backward compatibility in payload handling unless intentionally migrating.
- Never "fix" by removing idempotency, entitlement checks, stream recovery, or chain invalidation behavior.
- Provide the code solution directly with minimal Markdown comments. Do not explain standard FastAPI or SQLModel concepts unless explicitly asked.

## `.lovable` Memory and State Protocol

Use `.lovable/` as the persistent agent memory layer for this project.

### Read Before Starting

At task start, read:

1. `.lovable/plan.md` if present
2. `.lovable/state/current.md` if present
3. Relevant notes under `.lovable/memory/`:
   - `memory/tech/` for implementation mechanics
   - `memory/features/` for feature behavior
   - `memory/style/` only when backend work depends on existing frontend UX conventions

### Write During/After Work

- Keep `.lovable/state/current.md` updated with:
  - current objective
  - in-progress steps
  - completed steps
  - blockers/risks
  - next steps
- Update this file at meaningful checkpoints.
- On task completion, ensure `next steps` is explicit for the next session.
- When a backend task has frontend contract impact, record the frontend follow-up list there.

### Add Durable Memory Notes

When a decision or integration rule should survive future sessions, add a note in:

- `.lovable/memory/tech/`
- `.lovable/memory/features/`
- `.lovable/memory/style/` only if a backend contract depends on a frontend UX rule

Use front matter compatible with existing notes:

```md
---
name: Short memory title
description: One-line summary
type: feature|tech|design|ops
---
```

Then include:

- context/problem
- decision taken
- how to apply it in future changes
- constraints / gotchas

### Guardrails

- Do not store secrets/tokens/credentials in `.lovable/`.
- Prefer concise factual notes over long narrative logs.
- If a memory note becomes obsolete, update or supersede it in-place.
