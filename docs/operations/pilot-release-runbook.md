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

3. In BotFather, configure **Bot Settings → Web Login** with the browser origin and the exact backend callback URL. Store the resulting client ID and secret only in the backend deployment secret.
4. Confirm `WEB_AUTH_CALLBACK_URL`, `WEBAPP_URL`, CORS origins, secure cookie settings, trusted proxy CIDRs, SMTP delivery, Sentry DSN, frontend API URL, `VITE_SUPPORT_EMAIL`, bot username, and public-site URL.
5. Confirm `TELEGRAM_OIDC_ENABLED=true`, the Telegram OIDC client ID/secret/callback, `PASSKEY_RP_ID`, and exact `PASSKEY_ALLOWED_ORIGINS`.
6. Confirm the migration head is `w1a2b3c4d5e6` and take a PostgreSQL backup before applying it.

## Backend first

1. Run `alembic upgrade head` as the migration job.
2. Deploy the backend canary.
3. Verify Telegram browser OIDC login resolves the same internal user ID as the Telegram Mini App, then verify cookie reload, session listing/revocation, logout, and passkey enrollment/login.
4. Verify email request/verify and account export as fallback/lifecycle paths.
5. Verify a browser account can create a Telegram link challenge. Use dedicated test identities for linked and conflict outcomes.
6. Check Sentry, structured logs, 5xx rate, stream errors, payment webhooks, and database/Redis saturation before promotion.

## Browser and Telegram clients

1. Deploy the browser app canary with the backend URL and trust/support variables verified above.
2. Run the Playwright pilot suite against the canary URL.
3. Verify desktop and mobile Telegram login, email fallback, prompt handoff, reload, settings controls, support fallback, and logout manually once.
4. Open the Telegram Mini App with the same canary user and verify the same internal account, existing chats, plan/usage, browser-to-Mini-App and Mini-App-to-browser chat visibility, and send/stream.
5. Enroll a passkey, sign out, and sign back in with the passkey without opening Telegram or sending email.
6. Promote the frontend only while the backend canary remains healthy.

## Public trust pages

Deploy the SEO app and check `/privacy`, `/terms`, and `/account-and-data`, plus footer and sitemap links. Payment must remain disabled if operator details, price, renewal consent, cancellation, refund wording, or support delivery are not correct.

## Rollback

1. Abort or roll back the frontend first.
2. Abort the backend canary to the previously healthy ReplicaSet.
3. Keep migration `v1a2b3c4d5e6` in place during an application rollback: its columns and tables are additive and old code ignores them.
4. Downgrade the migration only after confirming there are no browser sessions, deletion markers, or Telegram link challenges that must be retained. Restore the pre-migration backup if downgrade validation fails.
5. Record release commits, build IDs, canary checks, rollback decision, and operator in the release log.
