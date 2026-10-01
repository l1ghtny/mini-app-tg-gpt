# Q01–Q03 production and beta release

Owner approved release of the reviewed frontend PR #21 before the next UX batch.
Production merge: `45633b3bfb6899f3dd9e84f7f10d059dec6c90dd` (reviewed head
`06c405ce700a0cd30257a2101d2898f691ff1f02`). Beta merge:
`f745ac3d6139eb585160f3293c057e1841df63cf`, preserving beta Work routes,
history and continuation action in the shared Chat actions menu.

## Versions and announcement decision

What's New: required. One grouped EN/RU item covers account tier identity,
restored mobile export/private link actions and the older-answer regeneration
warning. Item `2026-09-30-chat-quick-wins` was absent from the authenticated feed
before publication. No beta-only or pending row was inserted.

Frontend version: 2.0.1 -> 2.0.2 (compatible UX fixes). Backend stayed 2.0.1
during frontend deployment; 2.0.1 -> 2.0.2 for the later compatible announcement
migration. Product major remains 2. Backend master is the sole migration writer;
beta receives the same history and performs a schema check, not a second upgrade.

## Verified deployments

Production flow 9798 / build 112 succeeded. Frontend 9800 and deploy 9799
succeeded; unchanged backend 9694 / image 109 and migration 9691 were reused.
Frontend rollout has two Ready/Available pods and is Healthy, image 112.
Deployed bundle release: `tg-mini-frontend@2.0.2+112`.

Beta flow 9803 / build 223 succeeded. Frontend 9805 and deploy 9806 succeeded;
backend 9734 and shared-schema check 9736 were reused. Frontend image beta-223
is Ready; backend and Work worker beta-221 remain Ready. Deployed bundle release:
`tg-mini-frontend@2.0.2+beta-223`. Both backend images were at Alembic head 60
before announcement publication.

Public origins: https://app.lightnyai.ru and https://beta.app.lightnyai.ru.
The legacy https://app.lightny.ru timed out; its intentionally deferred routing
was not changed. `/health/ready` on the frontend origin serves SPA HTML, so its
HTTP 200 is not backend readiness proof. Direct backend `/health/ready` is ready.

## Executed acceptance

Owner explicitly approved a 30-minute test token and one synthetic chat with two
capped Luna replies per channel. Both public-origin API probes passed existing
account identity, allowance, signed estimates, no tools, done SSE, persisted
answers, duplicate request idempotency, resume 307/204, settled 204 and
Last-Event-ID acceptance. Each synthetic chat contains four messages. Evidence:
`/private/tmp/lightny-ux-release-production-api.json` and beta counterpart.

Production browser uses its existing authenticated session. Settings, allowance
and header show the actual private `Smooth tier` name plus `5× Start`. At 390×844,
the older-answer dialog names Luna and two later messages; Cancel preserves all
four messages and returns focus. Chat actions exposes Markdown export and the
private same-account web-link explanation; link copy reports success. The browser
download-event capture timed out, so actual downloaded file bytes remain unverified
in that browser. Reviewed export tests passed. Beta browser authentication is
pending the owner's passkey interaction. Desktop and beta UI checks remain pending.

Frontend review matrix: 463 tests, build and lint passed. Beta merge: 500 tests,
build and changed-file lint passed; an added Work action regression passed within
the six focused menu tests. The unchanged LazySyntaxHighlighter language-prop
TypeScript diagnostic remains the pre-existing full-tsc blocker.

Announcement: ten focused offline tests passed. A fresh local PostgreSQL 16.2
database ran the real integration test using its six table dependencies: upgrade
replay inserts one row, preserves publication time and publisher edits, EN/RU
authenticated feed text matches, and downgrade retains unrelated items. The full
repository fixture could not start because the bundled database lacks pg_trgm;
no test assertions or repository fixtures were weakened.

Sentry configured runtime IDs were verified from deployed frontend bundles and
API/bot configuration. Searches for the exact frontend and existing API releases
returned no received events in the acceptance window; observed event IDs are not
verified. Production source-map upload was not confirmed: the live Docker command
omits SENTRY_ORG/PROJECT arguments required by the Vite upload gate. These are
explicit observability limitations, not deployment or event-receipt claims.

## Remaining release work

Finish beta sign-in and desktop/mobile checks. Publish the grouped notice only
after production feature acceptance, verify the single head and localized feed,
then carry master history to beta and verify shared-schema compatibility. Record
the announcement deployment runs and final API/bot release IDs here. Resume
Q04/Q19/Q20/M16 only after both channels' feature verification; all new PRs stay
unmerged for review. Q07/Q11/public sharing remain deferred.
