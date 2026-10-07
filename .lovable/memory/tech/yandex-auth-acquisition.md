---
name: Yandex acquisition authentication and setup boundary
description: Canonical provider identities, direct auth transport, browser-bound attempts and owner registration requirements
type: tech
---

## Context and decision

Candidate batch1 isolates Telegram OAuth/JWKS clients from AI proxy environment routing, adds Yandex code+S256 PKCE browser login, and retains Telegram Mini App initData, email and passkeys. Provider flags default off; registration/secrets/live acceptance and deployment are owner/release tasks.

Yandex identity is `(provider="yandex", subject=userinfo.id)` after confidential code exchange and exact `userinfo.client_id` validation. Request only `login:info`; never infer verified email, bank-payer identity, or a merge from provider contact fields. Link requires the current canonical account and the same active browser session before and after exchange. Redis state is single-use with a per-attempt HttpOnly binding cookie. Preserve exact origin/return controls, safe expired-state host recovery, and fresh-attempt retries.

## Apply in future changes

- Keep sign-in and linking intent separate. Never create a second account/trial when adding a method to an existing account, or merge histories/payments/allowance lineage implicitly.
- Account-row locking covers identity/passkey removals; subject advisory locking covers concurrent Yandex signup. Preserve last-method rejection, revoked-session checks and starter serialization.
- Browser sessions rotate on authentication; provider tokens are ephemeral and not stored. Keep auth telemetry credential redaction and preserve error categories.
- Additive profile field `passkey_count` lets the frontend represent identity removal when a passkey remains. API/types/AuthGate/Settings/PasskeyRow/callback recovery and translations are coordinated in the paired candidate.
- Callbacks use the frontend host `/api/v1/auth/yandex/callback`. The binding cookie is host-only; a separate API-host start is rejected. CORS alone is not callback authorization. Beta membership/mutation guards remain enforced.

## Setup and evidence

Owner has only a personal Yandex account and no OAuth app at this checkpoint. Use a **user authorization / web services** application, minimal scope, exact registered production/beta/local callbacks, private `YANDEX_OAUTH_CLIENT_ID`/`YANDEX_OAUTH_CLIENT_SECRET`, and coordinated backend/frontend flags. Full checklist: `docs/operations/2026-10-07-yandex-auth-setup.md`.

Candidate migration74 extends the provider constraint. Downgrade refuses while Yandex identities exist rather than deleting login data. Source versions2.6.0 both; production basis2.5.1/backend133 and2.5.0/frontend130. No merge/deployment/registration/secrets changed. What's New is required at release but stays a documentation draft until actual production availability.

Local PostgreSQL/Redis tests and in-app desktop/mobile checks use synthetic identity/provider data. Do not claim real Yandex exchange, fresh real Telegram callback, physical-device/passkey acceptance, received Sentry2.6.0 releases, or production announcement from these checks. Next: review candidate PRs, owner secure setup and separately authorized live acceptance/release.
