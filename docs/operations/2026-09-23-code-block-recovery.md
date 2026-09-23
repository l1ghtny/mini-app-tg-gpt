# Code-block download recovery

The owner authorized fixing Sentry GPT-MINI-APP-FRONTEND-52 and shipping production and beta.

## Cause and fix

Vite resolves a failed dynamic import to `undefined` when a `vite:preloadError` listener prevents its default error. The existing stale-chunk reload handler does that while reloading. Destructuring the theme module then throws `Cannot read properties of undefined (reading 'oneDark')`, hiding the reply through the message error boundary.

Frontend `3851785` (production) / `03a70c0` (beta) makes the renderer one lazy module with direct Prism/one-dark imports. Suppressed imports and rejected downloads retain readable plain code; rejected downloads remain observable in Sentry. The original highlighted appearance and copy action are unchanged. There is no API contract change.

## Validation

- Production frontend: 366 tests; beta: 399 tests. TypeScript, focused ESLint, both builds and whitespace checks pass.
- `scripts/check-syntax-highlighter.mjs` builds a real Vite fixture and exercises healthy, rejected and suppressed downloads at 390/1440 px. Original source reproduces the exact oneDark error; fixed source passes all six scenarios.
- Deployed beta and production assets passed normal/failing-download chat rendering and clipboard checks at 390/1440 px with no uncaught browser errors. Screenshots reviewed. These use synthetic browser-intercepted API data; no customer chats, generation, billing or real login ceremony are exercised.
- Beta flow 9184 / #190 succeeded; manual head 9189 reused the same children. Frontend build 9186, deploy 9187, image beta-190; backend and schema jobs reused unchanged.
- Production frontend build 9198 / #75 uses exactly `3851785de0880988ac41cabe1eb86cab449ddd03`; image digest `sha256:88624ef91f70a5801ee6a9b32033292097c65eb4d80c9f6b977291985e4c5acf`. Flow 9195 / #75 succeeded with all three canary analyses Successful; the frontend Rollout is Healthy with two updated replicas. Both app.lightnyai.ru and app.lightny.ru passed browser acceptance.
- Beta frontend digest: `sha256:c42735fc21cb466367fda1686664bd107e59499deebdf961d01e4b4341e8be0e`.

## What's New gate

What's New: required. Replies remain readable when optional syntax highlighting fails, a visible reliability fix. Migration `xw0e1f2a3b4f` adds one bilingual item `2026-09-23-code-block-recovery`, without a CTA. Existing migrations/feed were checked for duplication.

Production frontend acceptance completed before this announcement commit/push. One Alembic head and offline SQL pass. Executing the migration twice against a transaction-scoped temporary clone of the feed table produces one item, both localized feed responses match, downgrade removes only that item, and rollback leaves the public feed unchanged. The standard release-notice replay test now includes this migration. Never run the repository-wide public-schema-reset fixture against the shared database.

Next: publish the notice through backend master, carry migration history to beta, and verify exactly one localized public feed entry in both channels. No future monitoring is scheduled by this task.
