# Startup loading and HTTP/2 release

User authorized two fixes: HTTP/2 on the dedicated Nginx server and fewer duplicate startup requests in production and beta. Upstream connection reuse and the separate UI-thread stall are excluded.

## Production feature verified

Frontend `f3d5eaf43621eb9dbe343dd6906776af093efbad` is fully promoted by TeamCity flow9291/#87; all four stages and canary analyses passed. Backend/frontend are Healthy with two updated ready replicas. Frontend image87 digest: `539d357007d762a75b7d915b3706e4fd599e0ea37f9dcc4f207fbffc58cbe56e`.

The API now shares in-flight model usage, settings, personalization and identical account/session onboarding visits. No completed-response cache. Writes remain independent and invalidate pending reads; account/token changes isolate pending work. Initial pageshow does not schedule a duplicate usage refresh; actual bfcache/focus/visibility resume still refreshes.

Nginx active and prepared production configs enable HTTP/2 for app.lightnyai.ru; the source template matches. Syntax check/reload passed and both public health routes negotiate h2. Backup `/var/backups/lightnyai-edge/http2-20260924T133320Z`. No upstream keepalive/session-reuse settings changed. Unrelated infrastructure working-tree changes preserved.

## Validation

Frontend385 tests, production build and changed-file lint pass. Only existing TypeScript failure remains in LazySyntaxHighlighter.tsx:30. Beta merge passes418 tests/build. Local, production canary and fully promoted public browser checks at390/1440 all produce one request per targeted startup endpoint, fetch fresh on reload and refresh usage on bfcache return. Chrome reports h2 for actual public assets. Every API in browser fixtures is intercepted and service workers blocked for deployed checks; no paid calls or real user settings changed. Mobile/desktop rendering reviewed.

## Announcement and beta

What's New: required. Production availability is verified; this release publishes one idempotent EN/RU item `2026-09-24-startup-loading` through migration `xw0e1f2a3b53`, following52. Two private-schema/offline tests, Ruff, single head and whitespace pass. Master is sole migration writer; beta carries the same migration and only verifies shared schema. Next: verify live localized feed, deploy beta branches and verify exact images, public health and browser counts.
