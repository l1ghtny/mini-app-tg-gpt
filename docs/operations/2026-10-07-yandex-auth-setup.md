# Yandex ID and dependable browser authentication — 7 October 2026

Status: review candidate — [backend draft PR40](https://github.com/l1ghtny/mini-app-tg-gpt/pull/40), [frontend draft PR34](https://github.com/l1ghtny/chat-bot-telegram/pull/34). No merge, deployment, OAuth registration, live credentials, payment policy, indexing or campaign settings have been changed. The owner currently has a personal Yandex account and no OAuth application.

## Owner checklist

1. Open [Yandex OAuth registration](https://oauth.yandex.ru/client/new/id/) under the personal account you can reliably recover. Select **For user authorization** (`Для авторизации пользователей`), then **Web services** (`Веб-сервисы`). An API/debug application cannot request the `login` permission group. Do not create an iOS/Android application for this browser flow.
2. Use the Lightny service name, its existing icon (at most 1 MB), and a monitored contact email. Select only **login:info** — login/name/profile information. Do not select email, avatar, phone, birthday, bank or other API permissions. The backend needs only the provider's `id` and `client_id`; it does not use name/gender or treat a Yandex contact address as a verified email login.
3. Register the exact callback URLs below. They are backend routes served on the browser app host. No wildcard, query, fragment, token-receiver HTML page or `verification_code` URL is needed.

| Environment | Redirect URI |
| --- | --- |
| Production | `https://app.lightnyai.ru/api/v1/auth/yandex/callback` |
| Beta | `https://beta.app.lightnyai.ru/api/v1/auth/yandex/callback` |
| Local preview / live local acceptance | `http://localhost:5187/api/v1/auth/yandex/callback` |

For stronger environment separation, use separate production and beta/local applications with the same single permission. The provider `id` remains the identity key; `psuid` is application-specific and is not used. With one application, all three exact callbacks must be registered. Keep the Client ID stable for each configured environment and recheck `client_id` validation when changing registrations.

4. Complete the console's save/confirmation steps yourself. [Registration guidance](https://yandex.ru/dev/id/doc/ru/register-auth) documents service name, icon, contact email, web redirect addresses and selected permissions. [Verification guidance](https://yandex.ru/dev/id/doc/ru/confirm-account) documents personal-account verification through Gosuslugi; without verification users see a warning. Owners without Russian citizenship can contact Yandex support through the linked guidance. Organization verification is a separate process and is not assumed for this personal account. No separate mandatory DNS/domain-verification step was established in the registration documentation. If the console requires moderation, or OAuth returns `unauthorized_client`, resolve the actual console status before live acceptance; a created application alone is not approval evidence.
5. Save credentials through the existing private backend configuration workflow. Do not paste a Client Secret, token, authorization code or secure env file into chat, Git, a PR, a screenshot or an unmasked CI parameter. Use the existing `backend-env` Secret for production and `backend-beta-env` for beta; this batch does not update either Secret. In a local secure environment file, use permissions 0600 and keep it outside tracked source.

| Backend setting | Value / rule |
| --- | --- |
| `WEB_AUTH_ENABLED` | Existing browser authentication must be enabled. |
| `YANDEX_OAUTH_ENABLED` | Default `false`; enable only for approved acceptance/release. |
| `YANDEX_OAUTH_CLIENT_ID` | Client ID from that environment's Yandex application. |
| `YANDEX_OAUTH_CLIENT_SECRET` | Client Secret, stored privately; this confidential backend flow uses it together with PKCE. |
| `YANDEX_OAUTH_REDIRECT_URI` | Exact callback on the canonical `WEBAPP_URL` host, from the table above. |
| `WEBAPP_URL` | The canonical frontend origin for that environment. |
| `WEB_AUTH_ADDITIONAL_ORIGINS` | Only explicitly approved additional HTTPS browser origins. CORS alone does not authorize callbacks. |
| `CORS_ALLOWED_ORIGINS` | The actual browser origin, needed for cookie-authenticated link/unlink mutations. |
| `AUTH_COOKIE_SECURE` / `AUTH_COOKIE_SAMESITE` | `true` / `lax` for HTTPS browser OAuth. Only the local localhost fixture uses `false` / `lax`. |

`YANDEX_OAUTH_STATE_TTL_SECONDS` is 600 and each provider HTTP operation has a 10-second timeout. These are code defaults. Yandex's documented code lifetime is also 10 minutes. The binding cookie is HttpOnly, host-only, SameSite=Lax and expires with the attempt; multiple tabs use independent cookie names.

6. Frontend builds need `VITE_WEB_AUTH_ENABLED=true` and `VITE_YANDEX_OAUTH_ENABLED=true`, and `VITE_API_URL` must point to the **same browser app host** that serves the callback. Source Docker and both pipeline templates accept the Yandex build flag, defaulting to `false`; the TeamCity build environment can supply `env.VITE_YANDEX_OAUTH_ENABLED` for a later approved build. These source templates are not evidence that installed TeamCity jobs have changed. Preserve existing deployed API/web URLs rather than replaying older URLs from a stale template.
7. Beta remains private. `BETA_ALLOWED_USER_IDS` and `BETA_ALLOW_IDENTITY_MUTATIONS` still apply. Do not broaden them to make a test pass. An existing allowlisted account can link a Yandex ID when identity mutations are approved; unallowlisted/new beta signup is denied before creating an account. Fresh signup acceptance can run locally with the registered local callback, or later in an explicitly authorized production pilot.
8. For local live provider acceptance, use frontend `http://localhost:5187`, proxy `/api` to the local backend (the review fixture uses 8087), and configure the matching local callback. Use `localhost` consistently; mixing numeric IPs, a different host, or a callback on a separate API host breaks the browser binding. Replace the synthetic preview provider with the unmodified backend and private real credentials before claiming live Yandex acceptance. The test-only preview files are ignored and never included in the PR.

## What changed

- Telegram token exchange and JWKS clients use `trust_env=False`; AI/provider routing and the global WARP configuration remain unchanged. Tests cover both clients under broken proxy env settings, and preserve PKCE, nonce and issuer/audience/signature verification.
- Yandex uses the [documented authorization-code + S256 PKCE flow](https://yandex.ru/dev/id/doc/ru/codes/code-url). The server exchanges the code privately and requests [user information](https://yandex.ru/dev/id/doc/ru/user-information) through the Authorization header. It pins the response `client_id` and uses only a validated provider `id`. Tokens are discarded after the request; no refresh token or Yandex bearer token is stored or returned to the browser.
- Login intent is independent of ambient account credentials. Linking has a separate authenticated endpoint, binds the starting browser session/account, and checks revocation again after the provider exchange under database locking. State is consumed once before exchange. Cancellation, expiry, replay, conflicting identities and session changes have distinct recovery messages. Retrying creates a new attempt.
- `UserIdentity(provider="yandex", subject=id)` resolves the existing canonical account. It never matches by email or payment details. Linking updates identities only; it does not issue a starter grant. Concurrent first logins and starter checks are serialized, and concurrent removals cannot remove all remaining methods.
- Browser authentication rotates/revokes the current browser session cookie on success. Existing logout and logout-other-devices remain available. Yandex grant revocation prevents future provider authorization; it does not replace Lightny session logout/revocation because provider tokens are not retained.
- Identity removal counts supported passkeys (retired RP credentials cannot justify removal of the last identity), and passkey deletion protects the last method. The profile adds optional `passkey_count`. Frontend Settings can add email recovery to a Yandex account, displays connected state, groups account controls, and confirms removals with an accessible in-app dialog. Backend rejection remains visible in that dialog if another tab changed available methods.
- The login screen tells existing Lightny users to sign in with their established method before adding Yandex ID, preventing accidental separate signup. Conflict recovery preserves both accounts and directs users to their existing login or another identity. Account merging is not implemented or promised. No histories, paid periods, allowance balances, payment links or consumed trial records move between accounts. Separate Yandex accounts are not bank-payer identity proof and do not solve trial deduplication across accounts.
- Auth request/provider credentials are redacted from Sentry request data, breadcrumbs, transactions and logs without suppressing error categories or unrelated diagnostics. Deployment operators should also keep callback query strings out of edge/access logs; the disposable preview runs with access logging disabled.

## Review and acceptance

Bases verified live on 7 October: backend `caf012782d7c5dc7c213cec5eb67fdcead07d432` / image133 / 2.5.1, frontend `c870cb57ddba406e31767c3b42c9515a4388bc75` / image130 / 2.5.0. Both production rollouts reported Healthy. Existing shared checkouts were preserved; candidates are isolated in `/private/tmp/lightny-yandex-auth-{backend,frontend}-20261007`.

Final backend acceptance: **71 passed** (plus20 final Yandex checks and the explicit return-query regression) with real PostgreSQL and local Redis, no skips. Frontend: **606 passed** (plus7 final focused UI fixture checks), production build passed; changed-surface ESLint has no errors and retains the prior AuthGate fast-refresh warning. TypeScript still reports only the existing LazySyntaxHighlighter TS2322, unchanged from the base. Pipeline YAML and changed build scripts parse/check successfully; TeamCity server-schema validation is unverified because the existing CLI session receives a CSRF403 from its schema-generation POST. No reauthentication or CI/settings write was attempted. The PostgreSQL suite exercises signup/repeat login, canonical-account linking/conflicts, existing email/Telegram/passkey paths, cookie rotation/logout, revoked links during exchange, concurrent first signup and concurrent removals, and unchanged seeded chat, confirmed payment, purchased subscription and fully consumed trial records. Real local Redis verifies attempt expiry. The migration is one Alembic head (`xw0e1f2a3b74`), offline SQL is checked, and live upgrade/downgrade proves that downgrade refuses to erase existing Yandex identities.

The Codex in-app browser uses the real local API/database/Redis with a clearly labelled **synthetic provider**. It covers mobile 390×844 and desktop 1280×900 login/Settings, fresh signup, reload, adding email recovery, linking, cancellation, confirmed removal and persistent conflict recovery. Screenshots are in `yandex-auth-20261007/`. It is not real Yandex approval/token-exchange acceptance or a physical device test.

Owner/device acceptance still required after configuration and a separately authorized release/real local run:

- Fresh Yandex signup and repeat login; reload, return after closing the browser, two tabs, logout and session revocation.
- Link Yandex to an existing email/Telegram account; check identical account ID, chats, paid period and trial consumption. Attempt conflicts with a separately owned test account; both accounts must stay intact.
- Add/verify email recovery, link Telegram through the bot, register and use a passkey on the actual supported domain/device, and confirm last-method protection.
- Cancel provider approval, wait past expiry, replay an already consumed callback, and retry from the visible login action. Each retry must start a new attempt; do not reuse callbacks.
- Fresh real Telegram browser approval → callback → HttpOnly cookie → authenticated `/api/v1/auth/me`. The earlier WARP restoration and this local proxy-independence test do not prove that live login.
- Supported mobile Safari/Android browser, desktop browser, and Telegram Mini App launches. Signed Telegram initData remains the Mini App path; normal browser OAuth must not replace it.
- Validate direct Telegram/Yandex auth endpoint reachability from the release environment. Do not disable a production proxy to run outage acceptance; use an approved isolated environment.

## Release gate

What's New: **required** for the coordinated release (new Yandex entry, explicit recovery/linking and visible last-method protection). Per frontend AGENTS, keep the draft in documentation; do not insert even an inactive beta/unreleased notice in the shared production feed. Prepare/publish the announcement migration only after both components and credentials are available in production and acceptance is verified.

Backend candidate version: **2.5.1 → 2.6.0**. Frontend candidate version: **2.5.0 → 2.6.0**. Compatible capability/workflow, shared major remains2. Recheck current deployed versions before any future release. Once real users rely on Yandex, preserve those login routes during rollback or feature pauses; disabling the backend flag or reverting to a backend without Yandex auth can lock out accounts with no alternate method. Schema downgrade deliberately refuses existing Yandex records. Source versions and tests do not establish Sentry runtime release IDs or live feed publication.

Pending announcement draft:

- EN title: “Sign in with Yandex ID”. Body: “Use Yandex ID to sign in from a browser. Connect it in Account & security to keep using your existing account, and add email or a passkey for recovery. Login methods can be disconnected while another method remains available.”
- RU title: «Вход с Яндекс ID». Body: «Входите в браузере с Яндекс ID. Подключите его в разделе “Аккаунт и безопасность”, чтобы пользоваться прежним аккаунтом. Для восстановления доступа добавьте почту или ключ доступа. Отключить способ входа можно, если остаётся другой.»

Frontend contract files updated in this batch: `src/lib/api.ts`, `src/types/index.ts`, `AuthGate.tsx`, `SettingsPanel.tsx`, `PasskeyRow.tsx`, callback recovery and translations. No SEO contract change is needed for the existing generic browser entry. Next: owner registration/secure configuration, review both draft PRs, then separately authorize beta/release acceptance. No deployment is authorized by this batch.
