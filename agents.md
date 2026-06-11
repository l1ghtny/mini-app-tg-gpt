# AGENTS Guide (Frontend + Backend)

This file is the working guide for agents in a two-repo PyCharm attached setup.

## Workspace Topology

- Frontend repo (usually attached in Pycharm IDE): [`G:\0\Coding_projects\frontend\gpt-mini-app-frontend-lovable`](G:/0/Coding_projects/frontend/gpt-mini-app-frontend-lovable)
- Backend repo (this root): [`G:\0\Coding_projects\Python\PycharmProjects\mini-app-tg-gpt`](G:/0/Coding_projects/Python/PycharmProjects/mini-app-tg-gpt)
- Shared project memory folder (in frontend repo): `.lovable/`

This `agents.md` lives in the frontend repo by choice, but it governs work across both repos.

## Repo Roles

### Frontend (`gpt-mini-app-frontend-lovable`)

- Stack: `React 18 + TypeScript + Vite + Tailwind + shadcn/ui + Zustand + TanStack Query`.
- Auth UX is Telegram-first (`AuthGate`), with debug login path for local/test mode.
- API integration is centralized in `src/lib/api.ts` with strong normalization/back-compat logic.
- Streaming UX is SSE-based with reconnect/resume support:
  - parser: `src/lib/sseParser.ts`
  - transport: `src/lib/sseClient.ts`
  - recovery/resume: `src/lib/streamRecovery.ts`
- Error and product telemetry is integrated with Sentry.
- Deployment includes canary routing hooks (`src/lib/canary.ts`, `k8s/`, `scripts/release/`).

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

## Current Architectural Principles (Observed in Code)

1. **Defensive compatibility over brittle strictness**  
   Both repos normalize aggressively to handle schema drift and historical values (model aliases, tool choice variants, image quality aliases, optional fields).

2. **Resilience in streaming paths**  
   SSE streams are treated as lossy transport: reconnect, resume by `Last-Event-ID`, and reconcile from server state when needed.

3. **Idempotency for billable operations**  
   Message send flow requires `client_request_id`; backend deduplicates via `RequestLedger` unique keys and can return existing result links.

4. **Entitlement-first feature gating**  
   Model/tool/image/document behavior is constrained by active tier/packs and pacing checks before generation begins.

5. **Mutable UX, immutable accounting intent**  
   Conversation history can be truncated/edited; accounting and entitlement consumption remain explicit via ledger state transitions.

6. **Observability as a first-class concern**  
   Sentry is wired on both sides; backend emits structured chain metrics and frontend captures stream/auth/runtime failures.

7. **Canary-safe rollout posture**  
   Header-driven canary routing (`X-Canary-User`) exists end-to-end in frontend header injection and backend ingress/rollout config.

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
- If backend validation changes, update frontend tool normalization and related tests immediately.

### Timeline rewrite semantics

- Edit/delete/regenerate operations truncate chat history after a target point.
- These operations must keep invalidating chain state (`last_openai_response_id` + fingerprint metadata).

### Image/document semantics

- Image generation and document search are entitlement-gated.
- Image partial/final stream events must stay compatible with `sseParser.ts` mapping logic.

## Where To Change What

- Backend endpoint or schema change:
  - update backend schema/router/helper
  - update frontend `src/lib/api.ts` normalizers
  - update frontend `src/types/index.ts` if shape changed
  - verify stream parser/recovery if event types changed
- New model/tier/entitlement behavior:
  - backend DB models/migrations + entitlement services
  - frontend settings defaults and catalog rendering paths
- Stream event change:
  - backend emitter (`openai_service.py` / `helpers.py`)
  - frontend parser (`sseParser.ts`) + client/recovery behavior

## Development Commands

### Frontend

- Install: `npm install`
- Dev server: `npm run dev`
- Tests: `npm run test`
- Build: `npm run build`

### Backend

- Install: `poetry install`
- Run API (common local): `poetry run fastapi dev main.py --host 0.0.0.0 --port 8080`
- Tests: `poetry run pytest`
- Migrations: `poetry run alembic upgrade head`

## Test Expectations

- Backend: prioritize `pytest` for any API/entitlement/streaming change.
- Frontend: run vitest for changed behavior; add tests for parser/normalizer logic when touching contracts.
- For cross-repo API changes, validate one real end-to-end send/stream/resume flow after unit tests.

## Env and Config Notes

- Frontend key envs are in `.env.example` (`VITE_API_URL`, Sentry vars, canary vars, debug vars).
- Backend settings are in `app/core/config.py` and related settings files (`redis`, `r2`).
- Do not hardcode secrets or environment-specific hostnames outside env-configured paths.

## Deployment Notes

- Frontend canary artifacts:
  - `k8s/argo-rollouts/tg-mini-frontend-rollout.yaml`
  - `scripts/release/deploy_canary.sh`, `promote_canary.sh`, `abort_canary.sh`
- Backend canary artifacts:
  - `k8s/argo-rollouts/tg-mini-backend-rollout.yaml`
  - migration job template: `k8s/migrate-job.yaml.tpl`

## Agent Quality Bar

- Keep changes narrowly scoped to requested behavior.
- Prefer existing patterns over new abstractions.
- Preserve backward compatibility in payload handling unless intentionally migrating.
- If you change a contract, update both repos in the same task and include migration notes.
- Never "fix" by removing idempotency, entitlement checks, stream recovery, or chain invalidation behavior.
- "Provide the code solution directly with minimal Markdown comments. Do not explain standard FastAPI or SQLModel concepts unless explicitly asked."

## `.lovable` Memory and State Protocol

Use `.lovable/` as the persistent agent memory layer for this project.

### Read Before Starting

At task start, read:

1. `.lovable/plan.md`
2. `.lovable/state/current.md` (if present)
3. Relevant notes under `.lovable/memory/`:
   - `memory/tech/` for implementation mechanics
   - `memory/features/` for feature behavior
   - `memory/style/` for UX/layout conventions

### Write During/After Work

- Keep `.lovable/state/current.md` updated with:
  - current objective
  - in-progress steps
  - completed steps
  - blockers/risks
  - next steps
- Update this file at meaningful checkpoints (not every tiny command).
- On task completion, ensure `next steps` is explicit for the next session.

### Add Durable Memory Notes

When a decision/pattern should survive future sessions, add a note in:

- `.lovable/memory/tech/`
- `.lovable/memory/features/`
- `.lovable/memory/style/`

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
- If a memory note becomes obsolete, update or supersede it in-place (do not keep conflicting guidance).
