# Current State

## Current objective

Triage and fix Sentry issue `GPT-MINI-APP-BACKEND-5B` on `GET /api/v1/user/usage/me/image-models`.

## In progress

- Backend patch is implemented locally to stop repeated pacing queries inside `get_image_usage()`.
- Focused regression coverage is added for the image-models response path.
- Local verification passed with focused pytest coverage and a syntax check.

## Completed

- Queried Sentry production issue `GPT-MINI-APP-BACKEND-5B` on June 23, 2026 in org `kosh-games`, project `gpt-mini-app-backend`.
- Confirmed it is a transaction/performance issue, not an exception:
  - issue type: `performance_n_plus_one_db_queries`
  - endpoint: `GET /api/v1/user/usage/me/image-models`
  - environment: `production_main_server`
  - release: `1.6.1`
  - count: `9`
  - affected users: `1`
- Traced the repeated query signature to `request_ledger` reads under the image usage path.
- Confirmed the backend cause in code:
  - `app/api/user_usage_helpers.py:get_image_usage()` called `check_image_pacing()` inside the resolution/source loop.
  - `app/services/subscription_check/pacing.py:get_image_energy_snapshot()` re-queries `RequestLedger` on each pacing check.
  - The entitlement payload already carries `energy_balance`, so the later per-resolution pacing checks were redundant.

## Blockers and risks

- This is a production performance signal, so local tests can verify the query path removal but cannot confirm Sentry issue closure without a deploy.
- Other endpoints may still have similar snapshot-recomputation patterns and would need separate production evidence before widening the fix.

## Next steps

- Run focused tests for the image usage helpers.
- If tests pass, re-check `GPT-MINI-APP-BACKEND-5B` after deploy for fresh events on or after June 23, 2026.

