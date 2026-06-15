# Current State

## Current objective

Add a reusable backend utility script that queries Sentry Explore table data for application metrics via the official organization events API.

## In progress

- Align the CLI with Sentry's actual `tracemetrics` query model from captured browser requests.
- Keep lightweight request diagnostics so empty output can be distinguished from API-empty results.
- Keep the change isolated from unrelated dirty backend files already in the working tree.

## Completed

- Read the local Sentry skill instructions and confirmed the script must rely on `SENTRY_AUTH_TOKEN`.
- Reviewed the official Sentry docs for `GET /api/0/organizations/{organization_id_or_slug}/events/`.
- Confirmed the repo already includes `requests`, so no dependency changes are needed.
- Added the initial generic parameterized CLI under `scripts/`.
- Captured the visible application metric names from the user's screenshots for a built-in `--all-known-metrics` mode.
- Confirmed that a truly empty API result would still print output, so the user's blank terminal likely points to invocation or transport/debuggability issues rather than just no matching rows.
- Confirmed from the user's captured requests that `tracemetrics` aggregates are keyed off `value` plus metric dimensions, not off direct expressions like `sum(app_opened)`.

## Blockers and risks

- Live verification against Sentry requires the user's local `SENTRY_AUTH_TOKEN` plus real org/project/query values.
- Sentry's table endpoint requires explicit selected fields, so there may not be a documented API path to auto-enumerate every custom application metric name in the org.
- The user's Sentry org is served from `https://de.sentry.io`, so local runs should set `SENTRY_BASE_URL` or pass `--base-url` to match that region.

## Next steps

- Run a local `--help` check to verify the script parses.
- Run local checks for the new metric-name mode and the built-in screenshot metric list.
- Share commands for both raw metric samples and tracemetrics aggregate fields using `https://de.sentry.io`.
- If needed, add a timeseries mode that matches the UI's `events-timeseries` request shape.
