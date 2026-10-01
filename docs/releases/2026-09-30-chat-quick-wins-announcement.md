# Held Q01–Q03 production announcement

**October 1: owner authorised publication; production frontend acceptance completed.**
Release evidence and remaining beta/telemetry checks are recorded in
`2026-10-01-chat-quick-wins-deployment.md`. The original hold below describes
the preparation boundary and is now satisfied for production publication.
The beta and production database is shared. This PR must not accompany the beta
feature deployment. No row, including an inactive draft row, has been written.

What's New: required because mobile export/link actions are restored, account
identity becomes consistent, and older-answer regeneration gains a warning.
Existing committed notices were inspected; this grouped notice uses a new stable
ID, `2026-09-30-chat-quick-wins`. Check the live feed for duplicates before publication.

The migration `xw0e1f2a3b60 → xw0e1f2a3b61` publishes one EN/RU improvement with no
CTA. `ON CONFLICT (id) DO NOTHING` preserves an existing item's publication time
and later edits. Downgrade deletes only that item. No API or accounting change.
The title/body are reviewable in the migration and are limited to Q01–Q03.

Production verified read-only on September 30: frontend `2.0.1+111` and backend
`2.0.1` (image 109). Frontend review PR prepares `2.0.2`. Backend remains `2.0.1`
for that UI release; **Backend version: 2.0.1 → 2.0.2** for this later announcement
publication, a compatible feed/data-migration change. Major remains 2. Recheck
deployed versions, complete release scope and the migration head before merging.

Executed: ten focused offline notice/timestamp tests, PostgreSQL Alembic offline
upgrade SQL, one head (`xw0e1f2a3b61`), Ruff and whitespace checks. No database
connection or upgrade ran. Disposable PostgreSQL upgrade-twice/scoped-downgrade
and authenticated feed visibility remain pending release checks.

After owner approval: beta feature acceptance; production frontend deployment and
feature/Sentry verification; then review/merge this PR and run master migrations.
Verify API/bot `2.0.2+<build>` Sentry releases and EN/RU feed visibility. Carry the
same migration history to backend beta without a duplicate write. Never infer
production availability from a build or a merged frontend PR alone.
