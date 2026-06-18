# Current State

## Current objective

Harden the admin broadcast backend into a safer job API and prepare a frontend handoff for a secure, functional, observable admin messaging UI.

## In progress

- Backend hardening is implemented and validated locally.
- Frontend admin UI still needs to be built against the current broadcast job API.

## Completed

- Kept the Redis-backed broadcast job model with durable job metadata, recipient snapshots, and recent-job history.
- Added preview-to-send audience confirmation via `expected_recipient_fingerprint` on broadcast create requests.
- Added idempotent job creation via `idempotency_key`, so duplicate admin submits can resolve to the same existing job instead of sending twice.
- Added cancel support with `POST /api/v1/admin/broadcast/jobs/{job_id}/cancel`.
- Added richer job audit fields:
  - `created_by_user_id`
  - `created_by_telegram_id`
  - `expected_recipient_fingerprint`
  - `idempotency_key`
  - `cancel_requested_at`
  - `cancelled_at`
- Added optional stronger access control:
  - `BROADCAST_ADMIN_TOKEN` remains required
  - if `BROADCAST_ADMIN_TELEGRAM_ALLOWLIST` is configured, the caller must also be an authenticated app user whose Telegram ID is allowlisted
- Updated the legacy `/panel` page so it can preview/store the fingerprint, submit an idempotency key, and request job cancellation.
- Added focused regression coverage for:
  - delivery outcome tracking
  - stale job marking
  - recent-job ordering
  - pre-cancelled job handling
  - idempotent job creation
  - recipient fingerprint mismatch rejection
  - allowlisted admin access enforcement
- Validated successfully:
  - `poetry run pytest tests/test_admin_broadcast.py`
  - `py -3 -m py_compile app/api/admin_broadcast.py app/core/config.py`

## Blockers and risks

- Delivery execution still runs in FastAPI background tasks, not a separate worker queue. Job state is durable, but active delivery can still stop on pod/node restart.
- There is still no real resume/retry engine for interrupted in-flight jobs.
- The optional Telegram allowlist only becomes effective when the frontend admin UI calls the API with the user bearer token.
- There is still no frontend admin UI consuming the job/recipient APIs.

## Next steps

- Build the frontend admin UI against the broadcast endpoints:
  - `POST /api/v1/admin/broadcast/preview`
  - `POST /api/v1/admin/broadcast/jobs`
  - `GET /api/v1/admin/broadcast/jobs`
  - `GET /api/v1/admin/broadcast/jobs/{job_id}`
  - `GET /api/v1/admin/broadcast/jobs/{job_id}/recipients`
  - `POST /api/v1/admin/broadcast/jobs/{job_id}/cancel`
- Ensure the frontend sends both:
  - `Authorization: Bearer <access_token>`
  - `X-Admin-Token: <admin token>`
  when `BROADCAST_ADMIN_TELEGRAM_ALLOWLIST` is enabled.
- In the frontend flow, require a preview step before enabling send, and pass the returned `recipient_fingerprint` back as `expected_recipient_fingerprint`.
- Generate and persist a client-side `idempotency_key` per send attempt so page refreshes/double-clicks do not create duplicate jobs.
- If stronger reliability is needed, move execution from FastAPI background tasks to a real durable worker queue.
