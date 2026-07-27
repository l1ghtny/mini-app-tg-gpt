# Closed-alpha release runbook

Release order is backend migration, backend canary, browser app canary, Telegram verification, then public trust pages. Do not promote a later stage when an earlier check is red.

## Preflight

1. Use a disposable PostgreSQL and Redis environment:

   ```bash
   cd /Users/lightny/0/coding/PersonalProjects/chat-bot-telegram
   pnpm test:e2e:local
   ```

2. In the production deployment environment, run without printing values:

   ```bash
   cd /Users/lightny/0/coding/PersonalProjects/mini-app-tg-gpt
   .venv/bin/python scripts/release/pilot_preflight.py
   ```

3. Confirm `WEB_AUTH_CALLBACK_URL`, `WEBAPP_URL`, CORS origins, secure cookie settings, trusted proxy CIDRs, SMTP delivery, Sentry DSN, frontend API URL, `VITE_SUPPORT_EMAIL`, bot username, and public-site URL.
4. Confirm the migration head is `v1a2b3c4d5e6` and take a PostgreSQL backup before applying it.

## Backend first

1. Run `alembic upgrade head` as the migration job.
2. Deploy the backend canary.
3. Verify email request/verify, cookie reload, session listing/revocation, logout, account export, and a harmless Telegram login.
4. Verify a browser account can create a Telegram link challenge. Use dedicated test identities for linked and conflict outcomes.
5. Check Sentry, structured logs, 5xx rate, stream errors, payment webhooks, and database/Redis saturation before promotion.

## Browser and Telegram clients

1. Deploy the browser app canary with the backend URL and trust/support variables verified above.
2. Run the Playwright pilot suite against the canary URL.
3. Verify desktop and mobile login, prompt handoff, reload, settings controls, support fallback, and logout manually once.
4. Open the Telegram Mini App with a canary user and verify login, existing chats, send/stream, and the account-link completion message.
5. Promote the frontend only while the backend canary remains healthy.

## Public trust pages

Deploy the SEO app and check `/privacy`, `/terms`, and `/account-and-data`, plus footer and sitemap links. Payment must remain disabled if operator details, price, renewal consent, cancellation, refund wording, or support delivery are not correct.

## Rollback

1. Abort or roll back the frontend first.
2. Abort the backend canary to the previously healthy ReplicaSet.
3. Keep migration `v1a2b3c4d5e6` in place during an application rollback: its columns and tables are additive and old code ignores them.
4. Downgrade the migration only after confirming there are no browser sessions, deletion markers, or Telegram link challenges that must be retained. Restore the pre-migration backup if downgrade validation fails.
5. Record release commits, build IDs, canary checks, rollback decision, and operator in the release log.
