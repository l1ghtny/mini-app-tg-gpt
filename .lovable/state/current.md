# Current State

## Current objective

Add a simple reusable backend utility script that queries Sentry Explore table data for metrics via the official organization events API.

## In progress

- Implement the new CLI under `scripts/` with env-driven auth and org defaults.
- Keep the change isolated from unrelated dirty backend files already in the working tree.

## Completed

- Read the local Sentry skill instructions and confirmed the script must rely on `SENTRY_AUTH_TOKEN`.
- Reviewed the official Sentry docs for `GET /api/0/organizations/{organization_id_or_slug}/events/`.
- Confirmed the repo already includes `requests`, so no dependency changes are needed.
- Chose a generic parameterized CLI shape instead of hardcoding one metric query.

## Blockers and risks

- Live verification against Sentry requires the user's local `SENTRY_AUTH_TOKEN` plus real org/project/query values.
- The available fields depend on the selected Sentry dataset, so example commands may need adjustment to match the user's data model.

## Next steps

- Run a local `--help` check to verify the script parses.
- Share the script path and a minimal example command.
- Ask the user for org/project/query details only if they want the example pre-filled for their exact metrics.
