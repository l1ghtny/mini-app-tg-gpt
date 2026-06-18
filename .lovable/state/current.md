# Current State

## Current objective

Upgrade admin broadcasts from a single active-subscriber filter into a safer campaign tool with Sentry-backed cohorts, broader audience scope, and cross-cohort cooldown protection.

## In progress

- Backend implementation is complete and validated locally.
- Frontend admin UI still needs to expose the new cohort filters and cooldown controls.
- Production use of the Sentry-backed cohorts depends on valid `SENTRY_AUTH_TOKEN` / `SENTRY_ORG` / `SENTRY_PROJECT` runtime config in the backend environment.

## Completed

- Added Sentry-backed audience resolution in [app/services/sentry_audiences.py](G:\0\Coding_projects\Python\PycharmProjects\mini-app-tg-gpt\app\services\sentry_audiences.py).
- Extended broadcast config in [app/core/config.py](G:\0\Coding_projects\Python\PycharmProjects\mini-app-tg-gpt\app\core\config.py) with:
  - `SENTRY_AUTH_TOKEN`
  - `SENTRY_ORG`
  - `SENTRY_PROJECT`
  - `SENTRY_BASE_URL`
- Extended broadcast filters in [app/api/admin_broadcast.py](G:\0\Coding_projects\Python\PycharmProjects\mini-app-tg-gpt\app\api\admin_broadcast.py) with:
  - `user_scope`
  - `registered_but_not_opened_within_hours`
  - `not_opened_for_days`
  - `min_hours_since_last_broadcast`
- Added `all_users` scope so broadcasts are no longer limited to active subscribers when running marketing / reactivation campaigns.
- Added cross-cohort cooldown filtering backed by Redis last-delivery timestamps, and persisted those timestamps on successful sends.
- Added observability for cooldown exclusions through `cooldown_excluded` on job and send responses.
- Relaxed recipient tier/onboarded metadata to allow all-user cohorts where no active subscription exists.
- Added focused regression coverage for:
  - Sentry cohort filtering
  - cooldown filtering
  - existing job-status and idempotency behavior
- Validated successfully:
  - `poetry run pytest tests/test_admin_broadcast.py`
  - `py -3 -m py_compile app/api/admin_broadcast.py app/core/config.py app/services/sentry_audiences.py`

## Blockers and risks

- Sentry-backed cohorts are only as good as emitted metrics:
  - `registered_but_not_opened_within_hours` is a stopgap based on `user_registered` minus `app_opened`
  - it does not cover existing users who pressed `/start` again unless bot-side `/start` instrumentation is added later
- The backend still lacks first-class durable `last_bot_start_at` / `last_app_opened_at` columns on `AppUser`.
- This shell still does not have direct production Kubernetes tooling installed, so prod verification remains external.

## Next steps

- In the frontend admin UI, expose the new payload fields:
  - `filters.user_scope`
  - `filters.registered_but_not_opened_within_hours`
  - `filters.not_opened_for_days`
  - `filters.min_hours_since_last_broadcast`
- Add clear preset labels in the UI:
  - “New users who registered but never opened the app”
  - “Users who have not opened the app for N days”
- Ensure production backend env includes:
  - `SENTRY_AUTH_TOKEN`
  - `SENTRY_ORG`
  - `SENTRY_PROJECT`
- Follow up with bot-side `/start` instrumentation for a better “started recently but did not open app” cohort.
- Longer term, add durable `AppUser` activity timestamps so Sentry is no longer the only audience source.
