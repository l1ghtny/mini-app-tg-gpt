---
name: Browser web authentication
description: Provider-neutral identities let browser and Telegram clients share the existing backend safely.
type: feature
---

## Context

The product now supports both the Telegram Mini App and a regular browser client. Browser users cannot be keyed by `telegram_id`, but they must reuse the same conversations, entitlements, billing ledger, and SSE pipeline.

## Decision

- `AppUser` remains the internal account and ledger owner; `telegram_id` is nullable.
- External login identifiers live in `UserIdentity` and are unique by `(provider, subject)`.
- Browser login uses single-use email magic links. Only a SHA-256 token hash is persisted, and challenges expire after a configurable TTL.
- Email callbacks put the opaque token in the URL fragment. The frontend removes it before telemetry initializes and requires an explicit confirmation click before calling the verification endpoint. Continue accepting legacy query-token links until all previously issued links have expired.
- Browser sessions use a Secure, HttpOnly, SameSite=Lax cookie; bearer JWTs remain supported for Telegram and compatibility. New frontend logins do not persist JWTs in local storage.
- An authenticated Telegram user can request an email link whose challenge targets that existing account, preventing accidental duplicate accounts.
- Cookie-authenticated mutations require an exact allowed Origin. Forwarded client IPs are trusted only when the immediate proxy belongs to `WEB_AUTH_TRUSTED_PROXY_CIDRS`.
- If an email already belongs to a different user, consume the challenge and return `account_merge_required`; never merge histories, subscriptions, payment state, entitlements, or ledger rows implicitly.
- Telegram-only operations must explicitly require a Telegram identity; shared chat and billing operations use the internal user UUID.

## Applying this in future changes

- Key ownership, entitlements, idempotency, and analytics by `AppUser.id`, never by `telegram_id`.
- Treat `telegram_id` as optional in API schemas and frontend types.
- Add new login providers through `UserIdentity`; do not add another provider-specific identifier to `AppUser`.
- Keep auth responses compatible with existing `access_token` normalization and keep SSE contracts unchanged.
- API, SSE, and protected binary requests from the browser must use `credentials: include`; SSE reconnect must also work when no bearer token exists in JavaScript memory.

## Constraints and gotchas

- Deploy migration `o1a2b3c4d5e6` before code that reads `user_identity`.
- Email auth is disabled unless both backend and frontend feature flags are enabled.
- Production needs SMTP sender configuration, an exact callback URL, and an explicit CORS origin.
- Keep click and open tracking disabled for authentication mail. Link previewers and security scanners must be able to open the callback page without consuming the challenge.
- Logout currently clears the cookie, but the JWT remains valid until expiry. Add server-side revocation/session rotation before treating session management as complete.
- Configure exact production origins, trusted proxy CIDRs, callback URL, and SMTP; never infer proxy CIDRs from a generic Kubernetes range.
- Existing separate Telegram/email accounts require an explicit merge/recovery workflow before seamless continuity can be promised.
