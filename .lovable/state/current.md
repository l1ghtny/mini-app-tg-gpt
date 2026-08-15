# Current State

## 2026-08-15 shared Work clarification schema

- Added only migration `xh5c6d7e8f9`, which creates the durable Work human-input
  table after the already deployed activity-timeline head.
- The migration does not alter or delete existing tables, columns, Work runs,
  artifacts, conversations, billing rows, or production history.
- Database constraints cap clarification at two rounds and allow only one
  pending question per Work run.
- Runtime behavior remains beta-only; this production commit exists solely so
  the shared PostgreSQL schema is safe before the beta runtime rolls out.

### Next steps

1. Run the normal production migration pipeline and verify Alembic reaches
   `xh5c6d7e8f9`.
2. Only then deploy the coordinated beta backend and frontend runtime commits.

## 2026-08-11 production R2 stream cleanup

- Centralized R2 response-body and client-context cleanup for normal stream
  completion, stream errors, and every setup failure after `get_object()`
  succeeds.
- Added regression coverage that forces response-header construction to fail
  and verifies both resources close with the original exception context.
- Bumped the backend Sentry release from `1.6.4` to `1.6.5`.
- Validation: ten focused image-proxy, R2-client, and release-wiring tests,
  changed-file Ruff, Python compilation, and `git diff --check` pass.

### Next steps

1. Confirm TeamCity deploys backend release `1.6.5` to
   `production_main_server`.
2. Exercise `/api/v1/images/proxy` against a tracked image and confirm Sentry
   issues `5X` and `5Y` do not recur.

## 2026-08-10 production R2 Last-Modified fix

- Sentry `GPT-MINI-APP-BACKEND-5X` traced to direct R2 image streaming passing
  an unnormalized `LastModified` datetime to `format_datetime(..., usegmt=True)`.
- Normalize aware and naive R2 timestamps with modern `datetime.UTC` before
  emitting the optional HTTP `Last-Modified` header.
- Added route-level and header-level regression coverage and bumped the backend
  release from `1.6.3` to `1.6.4` for post-deploy Sentry verification.
- Validation: six image-proxy tests and both version/entrypoint tests pass;
  changed-file Ruff, Python compilation, and `git diff --check` pass. The two
  remaining image-share tests require an unavailable `TEST_DATABASE_URL`.

### Next steps

1. Confirm TeamCity deploys backend release `1.6.4` from `master`.
2. Exercise a tracked R2 image through `/api/v1/images/proxy` in production.
3. Verify `GPT-MINI-APP-BACKEND-5X` receives no `1.6.4` events before resolving it.

## 2026-08-06 production image availability fallback

- The authenticated image proxy now streams tracked assets directly from R2 by
  their stored bucket and key instead of fetching their public URL through the
  application ingress.
- A tracked database row whose R2 object is gone is marked `missing` and returns
  HTTP 410. Expired and otherwise unavailable rows retain their existing 410
  behavior.
- The frontend tries the original image URL and proxy URL once each, then shows
  a muted localized unavailable tile. It does not expose retention details and
  does not emit a failure toast.
- Verification: backend proxy tests passed (5 passed, 2 unrelated tests
  deselected); frontend component tests, TypeScript, focused ESLint, and the
  production build passed.

### Next steps

1. Let the normal backend and frontend release pipelines consume the pushed
   default-branch commits; no deployment was performed from the temp clones.
2. Verify one retained image and one expired or missing image in production.
3. Confirm the `/api/v1/images/proxy` timeout issue stops accumulating in Sentry.

## 2026-07-31 backend health restart loop stopped

- Root cause: Argo applied the new HTTP health probes to backend image `251`,
  whose runtime rejected `/health/live` with HTTP 400, so the isolated canary
  repeatedly failed its startup probe and kubelet restarted it.
- Backend manifest commit `3a6fbad` stages the already successful TeamCity
  image `262` for the API only; the conversation-search worker and stable API
  replicas remain on `251`.
- Argo replaced the failing canary with `tg-mini-backend-74484d859c-km29t`.
  It is Ready with zero restarts; `/health/live` and `/health/ready` both return
  HTTP 200.
- The backend rollout is paused at step 2 with three Ready pods and no public
  canary weight. A canary-routed Telegram OIDC start request returns 302, while
  the unchanged stable image returns 404, proving isolation.

### Next steps

1. Smoke-test authenticated browser and Telegram Mini App flows against backend
   image `262` using the configured canary header.
2. Stage frontend image `119` at zero public weight and verify that browser
   Telegram login redirects through Telegram OIDC and returns with a session.
3. Promote backend and frontend only after the real cross-surface checks pass;
   update the conversation-search worker image separately.

## 2026-07-31 Telegram identity precedence hardened

- Frontend `AuthGate` now processes signed Telegram Mini App `initData` before web callback or browser-session restoration, so a persisted cookie for account A cannot override the current Telegram account B.
- A Telegram authentication failure no longer falls back to the stale browser cookie; retry remains possible from the unauthenticated Telegram error state.
- Telegram display/profile fields are only overlaid when the canonical backend user's Telegram ID matches the Telegram bridge user ID.
- Added four component regressions covering stale-cookie precedence, failed-Telegram no-fallback, missing signed data in a Telegram container, and unchanged ordinary browser-session restoration.
- Frontend verification: all 23 Vitest tests passed, production build passed, and ESLint completed with 0 errors and the same 29 pre-existing warnings.

### Next steps

1. Smoke-test the committed frontend inside a real Telegram iOS/Android client before deployment.
2. Deploy through the existing canary path and verify a known Telegram user's chat list after reload before promotion.

## 2026-07-31 Telegram Mini App regression audit

- Audited frontend commit `b3430a8` against the Telegram `initData` path: the passkey changes are confined to web/passkey handlers and do not change Telegram authentication, bearer tokens, chat, streaming, account lookup, or backend runtime code.
- Executed a correctly signed disposable Telegram Mini App login against the isolated backend, disposable PostgreSQL database, and live Redis. `/api/v1/auth/telegram` returned 200, issued a bearer token, and `/api/v1/auth/me` returned the same Telegram ID.
- Found and fixed one Mini App presentation regression from portaling toasts outside `#root`: the toast viewport now applies Telegram fullscreen safe-area variables directly, preventing notifications from overlapping device or Telegram UI insets.
- Focused backend authentication/passkey/identity suite: 23 tests passed in the shared test configuration; the one production-mode debug-delivery assertion also passed when run with explicit `TEST_ENV=0 DEBUG_MODE=0`.
- Frontend verification after the safe-area fix: all 19 Vitest tests passed, production build passed, and targeted ESLint passed.

### Next steps

1. Test the release candidate once inside a real Telegram iOS/Android client before production rollout; browser simulation cannot reproduce Telegram's native bridge lifecycle exactly.
2. Consider making Telegram `initData` take precedence over an existing browser-session cookie inside a Telegram container; the current restore-first order predates this passkey change but deserves a separate identity-safety review.

## 2026-07-31 passkey local-origin diagnosis and UX fix

- Reproduced passkey registration in the in-app browser and captured the exact WebAuthn failure: `SecurityError: This is an invalid domain.` The challenge endpoint returned successfully; the numeric `127.0.0.1` relying-party domain caused the browser rejection before verification.
- Frontend passkey registration and sign-in now detect numeric-IP origins before starting a ceremony, show actionable English/Russian guidance, classify browser/security failures, and report non-cancellation failures to Sentry.
- The custom toast provider now portals to `document.body`, so settings errors render above Radix dialogs instead of behind the modal. Browser screenshot verification passed.
- Local E2E web/auth/passkey origins now use `localhost`, including `PASSKEY_RP_ID=localhost`; the browser reached the native WebAuthn authenticator ceremony with backend challenge issuance and Redis state working.
- Frontend verification: all 19 Vitest tests passed, production build passed, and ESLint completed with 0 errors and 29 pre-existing warnings.

### Next steps

1. Complete the native authenticator confirmation and subsequent passkey sign-in in Safari or Chrome on the final HTTPS origin.
2. Ensure production `PASSKEY_RP_ID` is the deployed web domain and `PASSKEY_ALLOWED_ORIGINS` contains its exact HTTPS origin.

## 2026-07-30 single-friend browser acceptance complete

- Verified the configured Redis on `192.168.100.7` and dependency-aware backend readiness after Docker was enabled on the host.
- Ran the committed frontend locally against an isolated backend using `TEST_DATABASE_URL`; authenticated through the no-email debug magic-link UI and confirmed the HttpOnly browser session survives reload.
- Browser-tested onboarding, task workflows, real OpenAI chat/SSE completion and reload persistence, settings, Russian/light/dark UI, subscription overview/plans, document upload/indexing, and 390 px mobile layouts. Payments and Telegram linking were intentionally excluded.
- Found and fixed the document-workflow integration bug: the frontend attached documents but sent `default_tools: []`, disabling backend file search. The workflow now requests `file_search`; a real uploaded synthetic document returned its exact `ORANGE-731` code through OpenAI file search.
- Corrected Russian project search terminology and document-retention plurals; localized close, text-selection, and image-energy accessibility labels; added missing accessible titles/descriptions to the tested dialogs.
- Passkey registration options and challenge issuance work, but the in-app browser cannot complete the native Face ID, Touch ID, or device-PIN ceremony. Complete passkey acceptance in a normal Safari or Chrome session.
- Frontend verification after fixes: 17 Vitest tests passed, production build passed, ESLint completed with 0 errors and 29 pre-existing warnings. Browser logs have no new dialog accessibility warnings; only expected non-Telegram WebApp and React Router future warnings remain.

### Next steps

1. Test passkey creation and subsequent sign-in in Safari or Chrome on the final HTTPS origin.
2. Manually test Telegram linking and payments as explicitly reserved for the owner.
3. Configure production Telegram OIDC/passkey secrets and origins, migrate, deploy backend before frontend, then repeat the canary smoke test before promotion.

## 2026-07-30 closed-alpha Telegram browser login

- Added Telegram authorization-code OIDC with PKCE, one-time Redis state, nonce validation, JWKS signature verification, pinned issuer/audience, and safe local return paths.
- Browser Telegram login now resolves through the same `process_login` path as the Mini App and issues the existing HttpOnly browser-session cookie.
- Added release preflight checks for Telegram OIDC and passkey configuration; updated the release runbook to migration head `w1a2b3c4d5e6` and the real cross-surface acceptance flow.
- Focused backend tests pass (`14 passed` after the final OIDC verification test); PostgreSQL-backed browser session/linking/canonical Telegram identity tests pass (`4 passed` against `tg-bot-test`).
- Frontend follow-up implemented in `chat-bot-telegram`: Telegram is the primary browser sign-in, passkey and email remain available, and a successful Telegram login prompts passkey enrollment.
- Audited the complete backend/frontend dirty worktrees as one release candidate. Changed backend files pass Ruff, focused auth tests pass (`14 passed`), and the PostgreSQL identity/session suite passes (`4 passed`) against the isolated `tg-bot-test` database.
- The legacy full backend suite is not a clean repository-wide gate: three unchanged test files are non-UTF-8, many database tests require an explicitly exported `TEST_DATABASE_URL`, and two unchanged Google proxy tests currently fail independently of this tranche.
- With the disposable `TEST_DATABASE_URL` configured, simulated the production migration transition from `v1a2b3c4d5e6` to `w1a2b3c4d5e6`. Alembic initially detected a `created_at` nullability mismatch; the passkey model now explicitly matches the migration's non-null contract, and the final drift check reports no new operations.
- Reran all 18 changed authentication, passkey, Telegram OIDC, session and identity tests against the disposable PostgreSQL database after the schema fix; they pass.

### Remaining rollout configuration

1. In BotFather Web Login, register the browser origin and exact backend callback URL.
2. Add `TELEGRAM_OIDC_CLIENT_ID`, `TELEGRAM_OIDC_CLIENT_SECRET`, `TELEGRAM_OIDC_REDIRECT_URI`, `TELEGRAM_OIDC_ENABLED=true`, `PASSKEY_RP_ID`, and `PASSKEY_ALLOWED_ORIGINS` to the backend deployment secret.
3. Run the production preflight, migrate to `w1a2b3c4d5e6`, deploy backend before frontend, and perform the real Telegram browser/Mini App/passkey acceptance flow.

## 2026-07-29 home composer aurora visibility

- Restored the home-page composer glow without reintroducing the former rectangular blur artifacts.
- Root cause: the radial gradients still existed, but their focal point sat 170 px below the viewport, so the visible area contained only the nearly transparent tail behind the fixed composer.
- Raised the focal region, tightened the inner highlight, and increased the primary-color depth while keeping the implementation as layered CSS radial gradients with no blur filter.
- Verification: frontend production build and `git diff --check` pass; the running `http://127.0.0.1:4175/` preview returns HTTP 200 and serves the updated gradient values.

### Next steps

1. Visually review the authenticated dark-theme home screen after a hard refresh and fine-tune only the opacity if the new treatment feels too strong or too subtle on the user's display.

## 2026-07-27 pilot blockers P0.1-P0.5 implementation

- Added server-side, opaque browser sessions with a 30-day lifetime, logout revocation, session listing, individual revocation, and logout-other-devices. Bearer JWTs remain available for Telegram/API compatibility but are no longer stored by browser email/debug login.
- Added browser-initiated Telegram linking through a single-use 15-minute bot challenge. Unclaimed identities link explicitly; collisions preserve both accounts and return a support recovery reference. Identity unlinking prevents removal of the last login method.
- Added account JSON export and confirmed account deletion. Deletion disables renewal, revokes sessions, removes identities and chats, scrubs Telegram/payment credentials, and queues file/provider cleanup while preserving accounting records.
- Added migration `v1a2b3c4d5e6`, disposable PostgreSQL/Redis E2E composition, Playwright desktop/mobile auth/session/prompt/account-lifecycle coverage, integration tests, production environment preflight, and `docs/operations/pilot-release-runbook.md`.
- Added process-only `/health/live` and dependency-aware `/health/ready`; Docker and Kubernetes now use HTTP probes, while external AI providers remain outside pod restart readiness.
- Added public privacy, terms/payment, and account/data pages in `chat-search-link`, plus app login/settings/error support fallbacks, legal links, and the alpha warning against sensitive production uploads.
- Verification: backend Ruff, compile, and focused tests pass (`11 passed`; six database cases are skipped locally without disposable PostgreSQL); frontend TypeScript, seven unit tests, and production build pass; SEO production build passes; Playwright discovers eight Chromium/mobile tests. Full E2E execution remains pending because Docker/PostgreSQL are unavailable on this Mac.

### Next steps

1. Run `pnpm test:e2e:local` on a Docker-capable agent; fix any browser/data-path failures before commit or deployment.
2. Review the public privacy/terms copy with counsel and add a service address before public paid acquisition.
3. Commit each repository independently, run production preflight, deploy backend then frontend then trust pages through canary, and record build IDs/rollback evidence.

## 2026-07-27 real-user pilot readiness audit

- Added `docs/product-strategy/13-real-user-pilot-readiness-audit.md` after inspecting the earlier strategy pack, backend and frontend implementation, current local browser UX, test coverage, deployment manifests, and current competitor/category expectations.
- Verdict: the app is not ready for an unsupervised public beta, but it is close to a controlled 5–10 person alpha; the core chat/workspace is no longer the main blocker.
- Locally verified dummy email login, SEO prompt handoff through auth, task-first workflows, visible request quotas, Projects, document management, settings, and current subscription comparison.
- Highest-priority blockers: deploy the dirty worktrees safely, add production-equivalent E2E coverage, replace the four-hour non-revocable browser session lifecycle, choose an honest web–Telegram account contract, publish minimum privacy/file/deletion/support surfaces, add real HTTP health probes and restore evidence, persist browser acquisition attribution, and hide unimplemented Code Interpreter.
- Differentiation work for the research/document hypothesis remains navigable citations, source controls, page-level document citations, stronger exports/result reuse, and measured model guidance.
- Verification during audit: backend focused tests `12 passed, 3 skipped`; frontend Vitest `7 passed`; no live generation, upload, payment, refund, or destructive account operation was performed.

### Next steps

1. Execute Gate A from audit 13: clean commits, isolated migrations, session lifecycle, Code Interpreter removal, health endpoints/probes, and critical E2E matrix.
2. Execute Gate B: minimum trust/support pages and in-app links, account lifecycle process, cohort attribution, cross-surface contract, and restore/rollback drill.
3. Invite 5–10 named users to test sourced research, document analysis, and recurring Project work before adding broad competitive parity features.

## 2026-07-27 local web-app HTTP middleware fix

- Fixed local frontend requests from `http://127.0.0.1:4175`: the backend now trusts the `127.0.0.1` host and permits the exact port 4175 origin with credentials.
- Root cause was middleware rejection before routing: `TrustedHostMiddleware` returned HTTP 400 `Invalid host header`; the browser therefore surfaced `Failed to fetch`.
- Enabled `WEB_AUTH_ENABLED=True` in the local `.env`; `DEBUG_MODE` controls debug-token delivery but does not itself expose the web-auth routes.
- Verification: the exact email-login CORS preflight returns HTTP 200 with `Access-Control-Allow-Origin: http://127.0.0.1:4175`; the reloaded frontend login page no longer shows the transport error; Ruff passed and `tests/test_web_auth_security.py` reports 5 passed.

## 2026-07-27 explicit magic-link confirmation

- Changed production email-login callbacks from query tokens to URL-fragment tokens so the secret is not sent in the initial HTTP request or ordinary access logs.
- The frontend captures and removes callback secrets before Sentry initializes, while retaining compatibility with previously issued `?token=` links.
- Opening an email no longer consumes the challenge. The browser shows a dedicated confirmation screen and verifies the token only after the user explicitly continues.
- Updated English and native Russian login copy plus the plain-text email to explain the confirmation step.
- Added focused callback URL/parser tests. Verification: backend web-auth suite `8 passed, 3 skipped` without a disposable PostgreSQL database; frontend Vitest `7 passed`; targeted ESLint, Ruff, diff checks, and production frontend build passed.
- Resend production follow-up: use a verified Lightny transactional subdomain, disable click/open tracking for login messages, and migrate from SMTP to the Resend API with idempotency and signed delivery webhooks.

## 2026-07-27 competitive product implementation tranche

- Implemented a task-first home experience with five workflows: quick answer, writing/analysis, compare/decide, research with sources, and document analysis.
- Workflow selection chooses the best preferred model only when the current entitlement pool has capacity, otherwise preserving the user's current model. The workflow is stored on `RequestLedger` for cohort and conversion analysis.
- Added an in-context quota contract and pool preview: one completed text answer equals one request, with remaining requests or unlimited status shown before sending.
- Reframed existing chat folders as Projects in public UI copy; projects preserve shared instructions, grouped chats, and reusable documents that are attached automatically to new project chats.
- Added Markdown conversation export, bookmarkable `?chat=` URLs, and copyable browser links for cross-surface continuation.
- Added unauthenticated `GET /api/v1/public/catalog` as the backend-owned source for model, tier, pack, and billing-contract facts.
- Added cache-aware OpenAI cost telemetry: cached-input tokens, cache-write tokens, their separate costs, configurable pricing, and GPT-5.6 read/write rates. This fixes the measurement flaw that priced every input token as uncached.
- Fixed corrupted Russian display names for Sonar and Sonar Pro.
- SEO positioning commit `36757f8` was pushed to `chat-search-link/main` with corrected Premium pricing, completed-answer positioning, qualified continuity, updated workflow pages, dual CTAs, and `llms.txt`.

### Verification

- Backend focused tests: 19 passed, 3 PostgreSQL integration tests skipped without `TEST_DATABASE_URL`.
- Backend Ruff changed-file set: passed.
- Alembic graph: one head, `q1a2b3c4d5e6`.
- Frontend production build: passed; targeted ESLint has no errors and four pre-existing hook dependency warnings in `Index.tsx`.
- SEO build, changed-file lint, stale-claim search, and eight local route checks: passed before push.

### Immediate next steps

1. Run web-auth and new migration integration tests against disposable PostgreSQL.
2. Add app E2E coverage for workflow selection, quota preview, prompt handoff, export, browser deep links, and Telegram/email account linking.
3. Add provider-cost reconciliation against daily OpenAI billing totals before changing prices or quotas.
4. Deploy backend/frontend through canary after environment and SMTP/CORS preflight; do not deploy the unverified dirty worktrees directly.

## 2026-07-27 cost-analysis correction from OpenAI dashboard

- The supplied OpenAI dashboard shows $11.84 total project spend for the last 30 days, $8.22 July spend, 15,051,563 tokens, and 1,449 requests. This disproves the earlier estimate that `lloaThfull` alone cost about $23.20.
- Root cause: the reconstruction priced every stored input token at the uncached rate. The backend uses stored Responses and `previous_response_id` chaining, while current OpenAI cached input is priced at one-tenth of normal input. `UsageTracker` stores total input but discards `input_tokens_details.cached_tokens` and cache-write attribution.
- Reasoning tokens were not double-counted: `UsageTracker` subtracts reasoning from total output before persisting normal output tokens.
- A current production aggregate showed about 3.7 million stored OpenAI tokens for `lloaThfull` in the rolling window, but exact per-user dollars cannot be recovered without cached-token data. Their OpenAI cost is below the entire project's $11.84 and likely single-digit dollars.
- Withdrawn: the recommendation to reduce Premium Flagship from 100 to 40-60 or introduce a 3,990-4,990 RUB Power tier as a margin fix.
- Correct decision: keep Basic 490 RUB, Advanced 1,490 RUB, Premium 2,490 RUB, and current request allowances while adding cached/cache-write telemetry and reconciling daily provider costs.
- Updated product-strategy documents 11 and 12 plus durable memories to remove the invalid cost figures and pricing conclusion.

## 2026-07-27 concluding differentiation and viability decision

- Added `docs/product-strategy/12-concluding-promise-viability-and-value.md` and reconciled earlier strategy wording away from per-request ruble estimates toward clear request-pool impact.
- Final promise direction: “AI without token math. For text, one completed answer uses one clear request. Choose the level of intelligence, see exactly what remains, and continue the same work in the browser or Telegram.”
- Competitive conclusion: Chad, BotHub, GPTunnel, and Neuromia remain stronger in catalogue breadth, media types, pay-as-you-go flexibility, proof, or business readiness. Lightny can differentiate through tariff simplicity plus a stronger research/document workflow and verified web-Telegram continuity.
- Customer-value conclusion: recurring knowledge workers can receive better cognitive and budget value; occasional pay-as-you-go users, broad-media users, and enterprise teams do not yet universally receive better value.
- Commercial decision: preserve request-based subscriptions and keep all current pricing and allowances during corrected measurement. Request packs remain a convenience expansion, not a demonstrated margin necessity.
- Expansion priority: automatic model guidance, sourced research, serious document citations/collections/exports, verified cross-surface continuity, and request packs; Claude and comparison are parity; broad media remains later.

## 2026-07-27 production usage personas and unit economics

- Queried production read-only for request, token, model, web-search, image-energy, subscription, and conversation-count aggregates; no message, document, payment, email, or Telegram-ID content was inspected.
- Added `docs/product-strategy/11-usage-personas-and-unit-economics.md` and durable feature memory.
- Segmented the friend cohort into occasional convenience (`F0rvarD`), steady Fast utility (`flow_of_spirits`), episodic Balanced/search burst (`ARONZ96`), and daily Flagship/image power (`lloaThfull`) personas; an anonymized 203-request/five-day account provides an additional binge pattern.
- The original provider-cost reconstruction is superseded because it priced all input as uncached. Behavioral counts and personas remain valid; dollar estimates do not.
- Per-user dollar costs remain unknown until cached-input/cache-write tokens are stored or allocated from provider usage exports.
- Keep the public request-quota/no-token-counting promise. P0 is accurate cost telemetry plus invisible output, reasoning, context, tool-call, image-energy, and automation guardrails.
- Pricing decision: no quota or price change from the current evidence; revisit only after corrected 30-day paid-cohort reconciliation.

## 2026-07-27 production quota and competitor review

- Queried the production catalogue read-only from the `gpt` namespace and confirmed that paid text access is subscription-based, with monthly request allowances per shared model pool rather than per-request ruble charging.
- Public production prices and text pools are currently:
  - Basic: 490 RUB; Fast unlimited, Smart 300, Balanced 100, Flagship 15, Sonar 300, Sonar Pro 0.
  - Advanced: 1,490 RUB; Fast/Smart/Sonar unlimited, Balanced 250, Flagship 25, Sonar Pro 0.
  - Premium: 2,490 RUB; Fast/Smart/Sonar unlimited, Balanced 1,000, Flagship 100, Sonar Pro 100.
- GPT and Gemini models can share one pool: Fast combines GPT Nano with Gemini Flash Lite; Smart combines GPT Luna with Gemini Flash; Flagship combines GPT Sol with Gemini Pro.
- Failed text generations are finalized as refunded and do not consume quota. Monthly usage resets on the subscriber's billing-date boundary.
- Production currently has no public usage packs. Do not promise request top-ups until catalogue rows and checkout are enabled.
- Images are the exception to the simple monthly-request story: paid recurring tiers use replenishing daily image energy with a five-day storage cap and model/quality-dependent costs.
- Competitive review supports retaining subscriptions and request quotas as a differentiator against token-, credit-, Caps-, spark-, and wallet-based billing. Replace the earlier pre-request ruble-estimate recommendation with a quota-impact indicator such as pool used, requests remaining, and reset date.
- Product/UI follow-up: present shared usage pools rather than duplicate per-model limits; qualify unlimited plans with the existing technical rate limit; translate image energy into understandable generation estimates; and update stale public Premium pricing from 2,190 to the production value of 2,490 RUB.

## 2026-07-26 app-to-SEO capability audit

- Committed and pushed the current SEO acquisition tranche to `chat-search-link` `main` as `f2a681f` (`Improve browser-first SEO acquisition`).
- Compared the current backend, React app, and SEO site and added `docs/product-strategy/10-app-to-seo-capability-audit.md`.
- Strongest under-communicated product layer: workspace organization and continuity—conversation search, folders with shared prompts, persistent personalization, resumable streams, and a reusable pinned document library.
- Other safe secondary messages: automatic/manual tool selection, Sonar search depth and URL/finance tools, image controls and energy, transparent usage pools, and billing self-service.
- Product-copy bugs found: the frontend still promises Claude although Claude is absent from the backend registry; Code Interpreter is exposed in UI/schema but never added by the backend tool builder.
- Do not promise seamless web/Telegram account continuity, fixed file retention, security absolutes, or guaranteed stream recovery until the relevant production contracts and policies are verified.
- SEO P0 remains real product proof, application-side prompt handoff, public backend-driven pricing/catalogue, legal pages, final domain/canonical setup, consent-aware analytics, and webmaster tooling.

### Next SEO implementation sequence

1. Remove the stale Claude copy and hide or implement Code Interpreter in the product repos.
2. Add app-side SEO prompt prefilling and end-to-end funnel measurement.
3. Build a focused workspace page for folders, shared prompts, search, and personalization using real screenshots.
4. Rework the PDF page around the reusable document library and pinning.
5. Expand help, billing/refund, account-linking, and legal information pages.

## 2026-07-26 browser-auth hardening and verification

- Browser authentication now uses a Secure, HttpOnly, SameSite=Lax cookie while preserving bearer-token compatibility for Telegram and existing API clients.
- The frontend keeps new JWTs in memory only, restores browser sessions through `/api/v1/auth/me`, sends credentials on API/SSE/image requests, resumes streams after cookie-only reloads, and calls the backend logout endpoint.
- Cookie-authenticated mutations require an exact configured CORS origin. Canonical `lightny.ru`, `www.lightny.ru`, and `app.lightny.ru` origins and `*.lightny.ru` hosts are recognized by default.
- Optional authentication is strict: an absent credential is anonymous, while an invalid supplied bearer or cookie returns 401.
- Magic-link IP limiting only trusts `X-Forwarded-For` from configured proxy CIDRs; an untrusted shared ingress is not treated as one client. Email and global limits remain active.
- SMTP transport failures return controlled `login_email_unavailable` responses, and failed deliveries remove their challenge.
- Existing-email/account collisions preserve both users, consume the one-time challenge, and return `account_merge_required`; no conversation, subscription, payment, or ledger data is auto-merged.
- Magic-link callback tokens are removed from browser history before verification.
- The web-auth migration downgrade now renders in offline mode and refuses at execution time when web-only users exist.
- Verification completed: backend focused suite `7 passed, 3 skipped` (PostgreSQL integration cases require `TEST_DATABASE_URL`); Ruff passes with repository-wide FastAPI/style exclusions; offline Alembic downgrade SQL renders; frontend TypeScript, ESLint, Vitest (`3 passed`), and production build pass.

### Immediate release blockers

- Run all three skipped identity/database tests against an isolated PostgreSQL database and add API-level cookie/CORS/callback E2E coverage.
- Configure staging/production `WEB_AUTH_CALLBACK_URL`, SMTP sender credentials, exact `CORS_ALLOWED_ORIGINS`, `WEB_AUTH_TRUSTED_PROXY_CIDRS`, `AUTH_COOKIE_SECURE=true`, `VITE_API_URL`, and `VITE_WEB_AUTH_ENABLED=true`.
- Verify the real ingress proxy chain before enabling client-IP limiting; do not guess Kubernetes CIDRs.
- Implement an explicit, auditable account-merge/recovery workflow before claiming seamless continuity for users who already created separate Telegram and email accounts.

### Recommended next implementation sequence

1. Staging deploy and E2E matrix: new email login, reload, logout, expired/replayed link, Telegram login, Telegram-to-email linking, conflict behavior, chat send/SSE resume, documents/images, entitlements, and payments.
2. Account lifecycle P0: controlled merge preview/confirmation, immutable ledger/subscription rules, unlink-with-last-identity protection, recovery, server-side session revocation, and duplicate-account telemetry.
3. Browser activation P0: consume the SEO `prompt` handoff safely, add a guest/preview first-value path, and expose backend-driven public model/pricing/limit data.
4. Launch trust P0: privacy, terms/public offer, refund, file retention/deletion, provider-routing, and data-location disclosures with qualified review.
5. Measurement P0: auth funnel, email delivery, callback failures, link conflicts, duplicate accounts, first useful task, SSE reliability, cross-surface continuation, conversion, and contribution margin.
6. Differentiation after launch readiness: automatic model guidance, pre-request cost estimates, comparison mode, stronger citations/exports, then Claude if unit economics and demand support it.

## 2026-07-26 web + Telegram product migration

- Cloned the SEO repository to `/Users/lightny/0/coding/PersonalProjects/chat-search-link`.
- Added backend, frontend, and SEO as the three folders in `mini-gpt-telegram.code-workspace` and documented the SEO role in `AGENTS.md`.
- Repositioned the SEO site around one AI with UI product with two interfaces: a primary full web app and a Telegram Mini App.
- Added web-app CTAs, separate `cta_web_app_click` analytics, dual mobile CTA, and `VITE_WEB_APP_URL` configuration while preserving start-only Telegram links.
- Rewrote the 11 product pages, shared UI copy, disclaimers, `llms.txt`, and all three blog articles in natural Russian.
- Corrected product facts: Perplexity Sonar/Sonar Pro are used through Perplexity API; current backend models, document limits, image-energy gating, and independent-provider disclaimers are reflected in copy.
- Frontend follow-up: finish and verify email-to-Telegram account linking before promising shared history and subscription across both login methods.
- Release follow-up: publish privacy/terms/file-processing documents and configure production `SITE_URL`, `VITE_WEB_APP_URL`, web-auth callback, SMTP, and CORS.

## 2026-07-26 product-market strategy pack

- Added `docs/product-strategy/` as the cross-repository source of truth for positioning, competitor lessons, product gaps, landing/conversion, SEO, trust/legal readiness, branding/domains, execution, and measurement.
- Recommended a focused position: an AI workspace for research, documents, writing, and code with curated GPT/Gemini/AI-search capabilities in the browser and Telegram.
- Recommended browser as the primary acquisition, activation, and payment surface, with Telegram as the continuity and convenience companion.
- Recommended `Lightny AI` as the master-brand direction, subject to ownership, trademark, availability, and customer validation; preserve `@AIwithUIbot` for Telegram continuity.
- Proposed domain architecture: `lightny.ru` for the canonical marketing site, `app.lightny.ru` for the web app, `api.lightny.ru` for the backend, and an optional defensive `lightny.ai` redirect.
- Prioritized browser auth/account-linking verification, public pricing/catalogue, trust and legal pages, real product proof, analytics, and a first-value demo before buying significant traffic.
- Prioritized automatic model guidance, cost estimates, side-by-side comparison, Claude, and stronger document citations/exports as the focused product differentiation layer.
- Next decision: accept or revise the positioning and brand/domain architecture, then execute Phase 0 and Phase 1 from `docs/product-strategy/08-execution-roadmap.md`.
- Implemented the first strategy tranche in `/Users/lightny/0/coding/PersonalProjects/chat-search-link`:
  - rewrote the homepage around research, documents, and completed work
  - added public `/features`, `/models`, `/pricing`, `/security`, and `/help` routes
  - added browser-first navigation, model/pricing proof, WebApplication JSON-LD, Open Graph imagery, sitemap `lastmod`, and environment-aware `robots.txt`
  - SEO prompt CTAs now send a `prompt` query parameter to the web app for future composer prefilling
- SEO verification: targeted ESLint and production build pass; all new local routes return HTTP 200; desktop/mobile visual checks found no console errors or horizontal overflow.
- Frontend follow-up: read the optional `prompt` query parameter on app entry, validate its length, and prefill without automatically sending it.

## 2026-07-26 SEO browser-acquisition foundation

- Reworked the SEO homepage around research, documents, verified sources, and useful outcomes; the browser is the primary CTA and Telegram remains the companion surface.
- Added public `/features`, `/models`, `/pricing`, `/security`, and `/help` routes with native Russian copy grounded in current backend behavior.
- Added exact public monthly price anchors for Basic (490 ₽), Advanced (1,490 ₽), and Premium (2,190 ₽), while directing users to production for live limits and checkout terms.
- Added prompt handoff through the web CTA `prompt` query parameter; frontend follow-up is required to prefill the new-chat composer from this parameter.
- Added Open Graph images, `WebApplication` structured data, sitemap `lastmod`, new information routes in the sitemap, and an environment-aware dynamic `robots.txt`.
- Preserved `AI with UI` as the visible brand until the Lightny AI naming decision is confirmed.
- Verified a production build, lint for the changed-file set, desktop/mobile layout, navigation, pricing, dynamic robots, and sitemap. The local dev server is available at `http://127.0.0.1:4173/` for this session.
- Remaining SEO launch work: canonical domain decision, production `SITE_URL`, Yandex/Google webmaster setup, consent-aware analytics, reviewed legal documents, real product screenshots, and customer proof.

## Current objective

Deploy and canary the browser-ready authentication foundation across the backend and frontend.

## In progress

- None.

## Completed

- Added `teamcity-agent-new-node` with a dedicated 30Gi `microk8s-hostpath` PVC and stable `gamedev-teamcity-agent-new-node` identity.
- Kept the new build cache node-local for build performance; the other three agents remain independent fallback capacity if `new-node` fails.
- Reconciled the Docker-in-Docker insecure-registry arguments into the source manifest for all three extra agents.
- Verified the new pod is `2/2 Running` on `new-node`, Docker Engine is reachable, and Docker Compose is installed.
- TeamCity registered the agent as id `321` in the Default pool; it is connected and enabled but cannot be authorized under the current three-agent license.
- Audited live memory placement and all application hostPath PV/direct-hostPath consumers.
- Labeled `new-node` with `workload.cybercolors.dev/high-memory=true`.
- Added soft high-memory preference and `main-server` exclusion to the conversation-search worker and WARP proxy manifests.
- Live placement after rollout:
  - conversation-search worker on `new-node`
  - one WARP replica on `new-node`
  - one WARP replica on `k8s-node-3`
- Scheduling remains failover-capable because `new-node` is preferred, not required; node 2 and node 3 remain eligible.
- Verified both deployments rolled out successfully and the public GPT API remained HTTP 200.
- Inspected `gamedev` TeamCity Kubernetes objects:
  - existing `teamcity-agent-deployment` is a single-replica Deployment pinned to `main-server`
  - existing agent uses `hostPath` directories under `/opt/buildagent/...`, not PVCs
  - `teamcity-data-pvc` and `teamcity-logs-pvc` are mounted by `teamcity-server-deployment`
  - current agent uses fixed `AGENT_NAME=gamedev-teamcity-agent`, so scaling it directly would duplicate identity
- Added `k8s/teamcity-extra-agents.yaml` with two one-replica Deployments:
  - `teamcity-agent-k8s-node-2`
  - `teamcity-agent-k8s-node-3`
  - each has a dedicated 30Gi `microk8s-hostpath` PVC and stable unique `AGENT_NAME`
  - first version mounted `/var/run/docker.sock` from each node and persisted TeamCity conf/work/temp/tools/plugins/system/logs on PVC
- Added durable memory note `.lovable/memory/tech/teamcity-agent-scaling.md`.
- Applied the first manifest; both 30Gi PVCs bound on `k8s-node-2` and `k8s-node-3`.
- Verified both first-version pods are pending because the worker nodes do not expose `/var/run/docker.sock`.
- Applied the approved PVC-backed privileged `docker:29-dind` sidecar manifest.
- Verified both new pods are `2/2 Running` on `k8s-node-2` and `k8s-node-3`.
- Verified both agent containers can reach Docker `29.6.1` through their sidecars.
- Verified TeamCity registered the new agents as ids `311` and `312`; both are currently unauthorized.
- Observed first-run TeamCity plugin sync downloading into the persistent PVC-backed agent directories.
- Bumped `app/core/version.py` from `1.6.2` to `1.6.3` for the release.
- Investigated Sentry issue `82950975` / `GPT-MINI-APP-BACKEND-P`:
  - endpoint: `POST /api/v1/conversations/{conversation_id}/messages`
  - error: OpenAI `BadRequestError` for invalid `history_summary` JSON schema
  - root cause: `SUMMARY_JSON_SCHEMA` declared `open_tasks`, `constraints`, and `decisions` in `properties` but omitted them from `required` while `strict: True` is used
- Fixed `app/services/openai_service.py` so the strict summary schema requires every declared property.
- Added `tests/test_openai_service_schemas.py` to lock strict-schema requirements for title and summary schemas.
- Added production WhatsNew entry for the mobile text selection release:
  - id: `2026-07-01-mobile-text-selection`
  - kind: `improvement`
  - published_at: `2026-07-01T13:06:00`
  - title_en: `Mobile text selection is easier`
  - title_ru: `Выделять текст на телефоне стало проще`
  - inserted directly into production `whats_new_item` via the backend pod using Unicode escapes for Russian text
- Added production WhatsNew announcement directly to `whats_new_item`:
  - id: `2026-06-25-perplexity-ai-search`
  - kind: `feature`
  - CTA: `open_settings`
  - published_at: `2026-06-25T12:40:13`
  - note: first upsert succeeded but PowerShell mangled Cyrillic; immediately corrected `title_ru`, `body_ru`, and `cta_label_ru` using Unicode escapes and verified stored codepoints
- Fixed migration failure reported during `alembic upgrade head`:
  - root cause: migration used `ai_model_pricing`, but the existing SQLModel table is named `aimodelpricing`
  - patched `l1a2b3c4d5e6_add_perplexity_sonar_models.py` to seed/delete from `aimodelpricing`
- Added Perplexity config:
  - `PERPLEXITY_API_KEY`
  - `PERPLEXITY_API_BASE_URL`
  - `PERPLEXITY_SEARCH_CONTEXT_SIZE`
- Added `sonar` and `sonar-pro` to the backend text model registry under provider `perplexity`.
- Added `app/services/perplexity_service.py` to call Perplexity Sonar through OpenAI-compatible chat completions and normalize stream output into existing SSE event types.
- Wired `stream_normalized_ai_response()` to route Perplexity models to the new provider adapter.
- Kept Perplexity text-only:
  - rejects image input
  - rejects image generation
  - keeps file search restricted to OpenAI
  - uses the existing OpenAI image default for conversation/settings compatibility
- Added migration `l1a2b3c4d5e6_add_perplexity_sonar_models.py` to seed:
  - text model catalog rows
  - provider pricing rows
  - tier limits
  - usage pack limits where matching source limits exist
- Added focused tests in `tests/test_perplexity_provider.py`.
- Added durable memory note `.lovable/memory/tech/perplexity-sonar-provider.md`.
- Added Perplexity request controls:
  - `search_mode`: `quick`, `standard`, `deep`
  - `tool_choice`: `fetch_url`
  - `tool_choice`: `finance_search`
- Routed normal Perplexity AI Search through Sonar chat completions and mapped `search_mode` to `web_search_options.search_context_size`.
- Routed explicit `fetch_url` and `finance_search` tool choices through Perplexity Agent API.
- Normalized Agent API output into the same SSE event shape used by the rest of the app.
- Appended Agent API source URLs to assistant text.
- Stored Perplexity Agent API exact usage costs in existing token usage cost columns when `usage.cost` is returned.
- Kept `fetch_url` and `finance_search` scoped to Perplexity models through backend validation.
- Updated `.lovable/memory/tech/perplexity-sonar-provider.md` with the new tool/search-mode contract.

## Verification

- `poetry run pytest tests\test_openai_service_schemas.py tests\test_history_building.py tests\test_perplexity_provider.py tests\test_models_catalog_endpoint.py tests\test_tier_daily_display.py` passed: 22 tests, 1 warning from `google.genai.types`.
- `poetry run pytest tests\test_openai_service_schemas.py` passed: 2 tests.
- `poetry run pytest tests\test_history_building.py` passed: 7 tests, 1 warning from `google.genai.types`.
- `poetry run alembic upgrade head`
  - passed locally after the pricing table-name fix
- `poetry run pytest tests/test_perplexity_provider.py tests/test_google_provider_contracts.py tests/test_user_settings_endpoint.py tests/test_models_catalog_endpoint.py`
  - passed: 10 tests
- `poetry run pytest tests/test_perplexity_provider.py tests/test_models_catalog_endpoint.py tests/test_text_daily_reset.py tests/test_text_infinite_entitlement.py`
  - passed: 10 tests after the migration fix
- `poetry run pytest tests/test_text_daily_reset.py tests/test_text_infinite_entitlement.py`
  - passed: 5 tests
- `poetry run python -m py_compile app\services\perplexity_service.py app\services\ai_service.py app\services\model_registry.py app\api\chat_helpers.py app\api\model_catalog_helpers.py app\services\subscription_check\realtime_check.py migrations\versions\l1a2b3c4d5e6_add_perplexity_sonar_models.py`
  - passed
- `poetry run alembic heads`
  - current head: `l1a2b3c4d5e6`
- `poetry run python -m py_compile app\services\perplexity_service.py app\services\ai_service.py app\api\chat_helpers.py app\api\helpers.py app\schemas\chat.py`
  - passed
- `poetry run pytest tests\test_perplexity_provider.py`
  - passed: 7 tests
- `poetry run pytest tests\test_perplexity_provider.py tests\test_google_provider_contracts.py tests\test_user_settings_endpoint.py tests\test_models_catalog_endpoint.py`
  - passed: 13 tests

## Blockers and risks

- TeamCity agent `321` cannot be authorized because the current license allows only three authorized agents; either upgrade the license or replace/deauthorize an existing agent.
- The new agents use privileged Docker-in-Docker sidecars and expose the Docker API on `localhost:2375` inside each pod.

## Next steps

- Decide whether to license four concurrent TeamCity agents or replace the legacy main-server agent with agent `321`.
- Prefer one `tg-mini-backend` replica and selected stateless services toward `new-node` with soft affinity while preserving worker fallback.
- Replace main-server PGO replicas on worker-local storage before making the node control-plane-only; do not blanket-migrate synchronous databases to Longhorn without workload-specific latency validation.
- Review and remove confirmed obsolete, unmounted hostPath PVCs after backup/rollback retention decisions.
- Keep Prometheus on node 2 until its `emptyDir` TSDB has a deliberate persistence/migration plan.

## Latest update

- Checked the 2026-07-08 Telegram mini app reliability report against the SPB -> AmneziaWG -> k8s-node-2 path:
  - public `https://lightny.ru/` and `https://lightny.ru/api/v1/models/catalog` returned HTTP 200 during verification
  - SPB `awg0` is active as `10.77.0.2/24`; node2 endpoint is `152.53.33.181:41932`; latest handshake was recent and keepalive remains every 25s
  - SPB ping to node2 tunnel IP `10.77.0.1` was stable at ~33ms with 0% loss
  - SPB direct upstream probes through the tunnel to `gpt-mini-app.lightny.pro` and `gpt-mini-app-api.lightny.pro` returned HTTP 200 quickly
  - MicroK8s nodes and GPT backend/frontend rollouts are Healthy
- Found the real stability issue on SPB nginx:
  - `/var/log/nginx/error.log` had `768 worker_connections are not enough` alerts, including `2026-07-08 17:18:17 MSK`
  - the alerts were caused by bursts of `/lightny-connect` upgraded WebSocket connections sharing the same `lightny.ru` nginx worker pool as the mini app
  - live counts showed hundreds of established TLS connections and hundreds of local upstream connections to `127.0.0.1:10001`, enough to exhaust `worker_connections 768`
- Applied SPB nginx capacity tuning:
  - backup: `/etc/nginx/nginx.conf.codex-backup-20260708-173259`
  - added `worker_rlimit_nofile 65535`
  - added `worker_shutdown_timeout 30s`
  - raised `worker_connections` from `768` to `8192`
  - enabled `multi_accept on`
  - `nginx -t` passed and `systemctl reload nginx` succeeded
  - new nginx worker has `Max open files 65535`
  - public `lightny.ru` root/API and SPB-to-cluster tunnel probes stayed HTTP 200 after reload
- Updated `.lovable/memory/tech/lightny-spb-amneziawg-node2.md` with the nginx connection-exhaustion finding and mitigation.

- Fixed the 2026-07-04 `https://lightny.ru` Telegram mini app outage without abandoning the AmneziaWG tunnel:
  - root cause: SPB could not reach `main-server` (`152.53.95.40`) at the network layer, including UDP/TCP/ping, while `k8s-node-2` (`152.53.33.181`) was reachable
  - installed AmneziaWG packages/DKMS/tools on `k8s-node-2`
  - created a fresh node2 tunnel key and configured `k8s-node-2` as `10.77.0.1/24` on `awg0`, listening on UDP `41932`
  - updated SPB `/etc/amnezia/amneziawg/awg0.conf` to peer with node2 public key `0utAbOK0AFbrBqIozNoHlbckyHG3Qou4Ce+yJRnLiX0=` at `152.53.33.181:41932`
  - verified both SPB and node2 show recent AmneziaWG handshakes and traffic
  - stopped and disabled the old `main-server` `awg-quick@awg0` service to avoid duplicate tunnel ownership
  - added and applied `k8s/tg-bot-images-tcp-proxy-node2.yaml` so `/images/` keeps its `10.77.0.1:8443` TCP proxy after the tunnel endpoint move
  - verified public `https://lightny.ru/` returns 200, current JS/CSS assets return 200, and `https://lightny.ru/api/v1/models/catalog` returns the backend catalog
  - verified `/images/` no longer hangs; empty root path returns normal upstream 404/403 responses
  - cleaned up temporary `node-debugger-*` pods in the `default` namespace
  - added durable ops note `.lovable/memory/tech/lightny-spb-amneziawg-node2.md`

- Updated production `text_model_catalog` copy for Perplexity so subscription/usage surfaces describe it as AI Search:
  - `sonar`: `AI Search with sources` / `??-????? ? ???????????`
  - `sonar-pro`: `Deep AI Search` / `???????? ??-?????`
- Corrected the production Cyrillic update using Unicode escapes after raw PowerShell piping mangled Cyrillic output.
- Updated local `model_registry.py` fallback names and the Perplexity seed migration copy to match prod.
- Checked current mini app outage report on 2026-07-04:
  - Kubernetes cluster is healthy: `main-server`, `k8s-node-2`, and `k8s-node-3` are Ready.
  - Backend rollout `tg-mini-backend` is Healthy on image `localhost:32000/tg-mini-app-backend:254`.
  - Frontend rollout `tg-mini-frontend` is Healthy on image `localhost:32000/tg-mini-frontend-new:112`.
  - Public Cloudflare path `https://gpt-mini-app.lightny.pro/` returns 200 and serves current JS/CSS assets.
  - `https://lightny.ru/` resolves to `193.233.251.127`, establishes TCP/TLS, then returns no HTTP bytes until timeout.
  - Timed-out `lightny.ru` requests do not appear in Kubernetes ingress logs, so the immediate failure is before cluster ingress, likely on the SPB reverse proxy or its Amnezia/WireGuard route to the cluster.
  - Direct cluster ingress with Host `gpt-mini-app.lightny.pro` returns 200 on all three node IPs; direct Host `lightny.ru` returns 404 because Kubernetes has no `lightny.ru` ingress host.

## 2026-07-07 Sentry GPT-MINI-APP-BACKEND-5G

- `GPT-MINI-APP-BACKEND-5G` is a live DB DNS/service-discovery failure on `GET /api/v1/models/catalog`, not an OpenAI/Perplexity application bug.
- Sentry stack shows `asyncpg` failing to resolve/connect to `tg-mini-app-db-primary.gpt.svc:5432`.
- A public endpoint probe created a fresh event at `2026-07-07T11:37:14Z`; related issues `5F`, `5H`, `5J`, `5K`, and `5M` are DB DNS/timeouts in the same window.
- Kubernetes `/readyz?verbose` reported `etcd failed`, `etcd-readiness failed`, and informer/controller failures; subsequent `kubectl get` calls timed out.
- User started rebooting `main-server`; next checks are API readiness, DB service DNS, backend catalog endpoint health, and Sentry silence after reboot.

## 2026-07-07 main-server pressure relief

- Scaled AFFiNE down to stop the Prisma migration loop:
  - `affine/deploy affine` -> 0 replicas
  - `affine/deploy redis` -> 0 replicas
  - `affine/statefulset postgres` -> 0 replicas
  - force-deleted stuck AFFiNE pod objects after they remained Terminating
- Scaled `gamedev/deploy teamcity-agent-deployment` to 0 replicas to stop the legacy TeamCity agent pinned to `main-server`.
- Force-deleted the stale terminating legacy TeamCity agent pod object.
- Cordoned `k8s-node-3` while it was `NotReady`; `main-server` was temporarily uncordoned because public ingress depends on a hostPort ingress pod there.
- Backend service endpoints are clean: `gpt/backend` points only to `10.1.140.114:8000` on `k8s-node-2`.
- Direct ingress on `k8s-node-2` returns 200 for `https://gpt-mini-app-api.lightny.pro/api/v1/models/catalog`, but direct/public traffic to `main-server` times out because the main-server ingress pod was crashlooping and then did not recreate.
- Ingress DaemonSet is stale: `metadata.generation=3`, `observedGeneration=2`, `desired/current=3`, visible pods=2. Next required console action on `main-server`: `sudo systemctl restart snap.microk8s.daemon-kubelite`, then verify ingress DaemonSet reaches 3 ready pods.

## Incident checkpoint - 2026-07-07T13:36:12.1383307+01:00

- Sentry issue GPT-MINI-APP-BACKEND-5G was traced to DNS/DB connectivity failures during MicroK8s/control-plane instability, not an application-code regression.
- Reduced main-server pressure by scaling AFFiNE workloads to zero and scaling the legacy TeamCity agent on main-server to zero; backend service currently has a healthy endpoint on k8s-node-2 (10.1.140.114:8000).
- main-server is Ready and API /readyz passes after containerd/kubelite restarts, but normal ingress DaemonSet remains stale (desired=3, eady=2) and has not recreated the main-server ingress pod.
- Temporary ingress/nginx-ingress-main-manual host-network pod is Running and can route locally to /api/v1/models/catalog, but external traffic to 152.53.95.40:80/443 still times out.
- Next action: inspect/fix main-server host firewall/routing for ports 80/443, or repoint public origin/Cloudflare to k8s-node-2 (152.53.33.181) as the fastest restore path.

## Incident checkpoint - 2026-07-07T13:48:32.8730506+01:00

- After main-server reboot, Kubernetes API readiness passes and main-server is Ready.
- Public API still fails with Cloudflare 522; direct main-server origin probe to 152.53.95.40:443 cannot connect.
- Direct k8s-node-2 origin probe to 152.53.33.181:443 returns 200 for /api/v1/models/catalog.
- Temporary host-network ingress pod on main-server is Running, but normal MicroK8s ingress DaemonSet remains stale at desired=3/ready=2 with no regular main-server ingress pod.
- Next recommended restore: repoint public origin/Cloudflare for gpt-mini-app-api.lightny.pro to k8s-node-2 (152.53.33.181) or inspect/fix main-server host firewall/routing for inbound 80/443.

## Incident checkpoint - 2026-07-07T15:33:18.7456677+01:00

- Stopped snap.microk8s.daemon-kubelite on k8s-node-3 via operator action; controller-manager and scheduler leadership moved to main-server.
- Calico kube-controllers recreated on main-server and normal ingress pod ingress/nginx-ingress-microk8s-controller-j8lsq is Running on main-server.
- CoreDNS has one working endpoint on main-server (10.1.10.236), restoring DNS for main-server workloads.
- Public https://gpt-mini-app-api.lightny.pro/api/v1/models/catalog is stable again: five consecutive probes returned HTTP 200.
- Temporary mitigation: node2 backend pod 	g-mini-backend-869b4977b-k28p2 was relabeled pp=tg-mini-backend-isolated so broken node2 DNS does not cause backend Service flapping; backend Service currently routes only to main-server backend 10.1.10.251:8000.
- Remaining issue: k8s-node-2 reports Ready but does not start new pods; CoreDNS replica and replacement backend pod are Pending there. Next action: restart snap.microk8s.daemon-containerd and snap.microk8s.daemon-kubelite on k8s-node-2, then restore the node2 backend label or delete the isolated old pod once replacement is healthy.

## Incident checkpoint - 2026-07-07T15:55:00+01:00

- Operator restarted `snap.microk8s.daemon-containerd` and `snap.microk8s.daemon-kubelite` on `k8s-node-2`.
- Codex cannot SSH into `main-server` yet: `lightny@152.53.95.40` is reachable but rejects the available public keys.
- Public API remains healthy after the node2 daemon restart: five consecutive probes to `https://gpt-mini-app-api.lightny.pro/api/v1/models/catalog` returned HTTP 200.
- Next action: verify node2 readiness, CoreDNS readiness on node2, and DNS resolution from a node2 backend pod before restoring the temporarily isolated backend endpoint.

## Incident checkpoint - 2026-07-07T16:05:00+01:00

- Headlamp is reachable again and shows `main-server` plus `k8s-node-2` Ready; `k8s-node-3` remains NotReady with unschedulable/unreachable taints.
- Codex reached `152.53.16.43` over SSH but key auth failed for `lightny`; direct node3 inspection is blocked until a public key is authorized or command output is pasted back.
- Public API remains healthy: three consecutive probes to `/api/v1/models/catalog` returned HTTP 200.
- Recommended node3 recovery: restart/inspect `snap.microk8s.daemon-containerd` and `snap.microk8s.daemon-kubelite` on node3, wait for Ready, verify CoreDNS/Calico/ingress, then uncordon only after node health is stable.

## Incident checkpoint - 2026-07-07T16:48:00+02:00

- Operator-provided cluster output shows all three nodes have recovered to Ready.
- `k8s-node-3` is `Ready,SchedulingDisabled`; its only remaining taint is `node.kubernetes.io/unschedulable:NoSchedule` and Ready=True, NetworkUnavailable=False, with a renewing lease.
- Calico is Running on all nodes, CoreDNS is Running on main-server and k8s-node-2, and ingress controller pods are Running on main-server, k8s-node-2, and k8s-node-3.
- Next action: uncordon `k8s-node-3`, then verify node status, ingress daemonset readiness, backend endpoints, and public API health.

## Incident checkpoint - 2026-07-07T17:05:00+02:00

- CyberColors database inspection shows `cybercolors-db-instance1-5r9k-0` is healthy `2/2` on `main-server` and appears to be Patroni leader/primary.
- `cybercolors-db-instance1-7pb7-0` on `k8s-node-2` and `cybercolors-db-instance1-qfqz-0` on `k8s-node-3` are `1/2` because their `database` container readiness returns 503; the sidecar is ready.
- Logs show standby startup/catch-up failure on at least `7pb7`: it follows leader `5r9k` but waits for WAL at `C/74000040`, while primary reports no more WAL on that timeline and pgBackRest archive retrieval is not usable (`archive-get command requires option: pg1-path`).
- Recommended fix: verify Patroni roles with `patronictl list`, then reinitialize only the affected replica member(s) with `patronictl reinit`; do not restart/delete the primary.

## Incident checkpoint - 2026-07-07 main-server memory balancing

- Cluster is service-healthy again per operator report and public API probes; public `/api/v1/models/catalog` returned 200 in three consecutive checks.
- Headlamp shows `main-server` still has the highest memory pressure, about 10.2Gi/15.6Gi (65.6%).
- Codex still cannot SSH to `main-server`; key auth for `lightny@152.53.95.40` is rejected, so live pod/process memory attribution requires operator command output or SSH authorization.
- Next action: identify main-server memory owners with `kubectl top` plus host `ps`; avoid full drain because main-server runs control-plane and DB/ingress responsibilities. Prefer cordoning or workload-level scaling/migration for movable non-critical workloads.

## Incident checkpoint - 2026-07-07 main-server memory attribution

- Operator-provided resource output shows `main-server` at 10547Mi/66%, `k8s-node-2` at 6483Mi/42%, and `k8s-node-3` at 3680Mi/23%.
- Biggest pod memory users cluster-wide: YouTrack (~2009Mi), TeamCity server (~1651Mi), Prometheus (~1224Mi), CyberColors indexer worker (~818Mi), TeamCity agents on node2/node3 (~761-928Mi).
- Main-server pod list confirms many local-PV/stateful workloads are pinned there: TeamCity server, YouTrack, multiple Postgres/DB pods, Redis, Loki, Prometheus, registry, VPN DB, and app pods.
- Host process list matches pod attribution: YouTrack Java RSS ~1.96Gi, TeamCity Java RSS ~1.54Gi, Prometheus RSS ~1.33Gi, CyberColors AI worker Python RSS ~0.92Gi, kubelite RSS ~0.91Gi.
- Do not cordon/drain main-server globally because local-PV workloads and control-plane responsibilities depend on it. Use targeted stateless workload placement away from main-server plus memory limits/tuning for TeamCity, YouTrack, Prometheus, and indexer.

## Incident checkpoint - 2026-07-07 storage and Prometheus plan

- Prometheus currently runs with `--storage.tsdb.retention.time=10d`; setting retention to `15d` would increase retention, not lower it.
- Prometheus is managed by the live kube-prometheus stack rather than manifests in this backend repo; change retention through the Prometheus CR or Helm values, then persist in the cluster GitOps/Helm source of truth.
- For node-agnostic non-database PVCs, `microk8s-hostpath` is the blocker because it creates node-affine local storage. Use a shared/distributed CSI storage class for movable app PVCs, such as Longhorn, Rook-Ceph, NFS CSI, or an equivalent Hetzner/network-backed CSI.
- Databases should stay on database-native HA/replication or operator-managed storage; do not treat PostgreSQL PVCs as freely movable unless the storage backend and DB operator explicitly support the failure mode.

## Incident checkpoint - 2026-07-07 Longhorn sizing discussion

- User wants to move non-database app PVCs from `microk8s-hostpath` to Longhorn and eventually delete the 500GB disk from `main-server`.
- Nextcloud should be excluded from capacity analysis because it is not needed currently.
- Repository-visible PVCs only show TeamCity extra agents at 30Gi each and mini-app Postgres data at 10Gi per replica; most live PVC sizes/usages are cluster state and need `kubectl get pvc,pv` plus filesystem usage for accurate sizing.
- Planning guidance: with dedicated Longhorn disks, reserve around 10% free space; replica count 3 costs 3x raw usage, replica count 2 costs 2x and is a practical default for non-DB app PVCs with backups.
- Preliminary recommendation before exact PVC inventory: 3x200GB dedicated disks is the practical minimum for non-DB app PVCs excluding Nextcloud; 3x300GB is safer if TeamCity/YouTrack/Prometheus/Loki/registry history should have growth room. 3x100GB is likely too tight.

## Incident checkpoint - 2026-07-07 Longhorn disk inventory

- Operator added 300GB secondary disks to each of the three servers; disks still need partitioning/mounting for Longhorn.
- PVC inventory excluding Nextcloud requests about 332Gi total. Main-server non-Nextcloud PVC requests about 232Gi, including databases and app PVCs.
- Non-database/non-Nextcloud app PVCs are about 156Gi requested: TeamCity agents/data/logs, YouTrack, registry, Affine config/storage, Teamspeak, VPN/XUI app PVCs, and small Redis-like state.
- With three 300GB disks, practical Longhorn usable capacity is roughly 376Gi logical at replica count 2, or 251Gi logical at replica count 3 after reserving around 10% free space. Therefore 3x300GB is sufficient for non-database app PVCs with r2, but too tight for all non-Nextcloud PVCs at r3.
- Recommended rollout: mount each new disk at `/var/lib/longhorn`, install Longhorn prerequisites, create `longhorn-r2` for normal app PVCs and keep DB PVC migration separate.

## Incident checkpoint - 2026-07-07 Longhorn disk mount prep

- New empty Longhorn disks identified by operator output: `main-server` has `/dev/vdc` at 300G; `k8s-node-2` and `k8s-node-3` have `/dev/vdb` at 300G.
- Existing `main-server` `/dev/vdb` is the 500G `/mnt/second_drive` Nextcloud disk and must not be formatted during Longhorn setup.
- Codex SSH remains blocked on all nodes due to rejected public keys, so disk formatting/mounting must be run by the operator unless SSH access is authorized.
- Added `k8s/longhorn-storageclasses.yaml` with `longhorn-r2` and `longhorn-r3`, both using `Retain` reclaim policy and ext4.
- Next operational step: format and mount the new 300G disks at `/var/lib/longhorn` on all nodes, install `open-iscsi` and `nfs-common`, install Longhorn, apply storage classes, then migrate one low-risk app PVC first.

## Incident checkpoint - 2026-07-07 Longhorn disks mounted

- Operator reported completing the Longhorn disk mounting step after mounting `/var/lib/longhorn` on `main-server` and then repeating on the other two nodes.
- Codex still cannot SSH to verify mounts directly; public API remained healthy during the check.
- Next step: verify `/var/lib/longhorn` on all nodes, install `open-iscsi`/`nfs-common`, install Longhorn v1.12.0 from the official manifest, apply `longhorn-r2`/`longhorn-r3` StorageClasses, and validate Longhorn nodes/disks before migrating PVCs.

## Incident checkpoint - 2026-07-07 Longhorn driver deployer MicroK8s fix

- Longhorn managers, engine images, instance managers, UI, and node disks are running/registered on all three nodes.
- `longhorn-driver-deployer` is CrashLooping because it cannot auto-detect kubelet root: logs say `failed to get arg root-dir. Need to specify "--kubelet-root-dir"` after failing to find normal `kubelet` or `k3s` processes.
- Cause: MicroK8s uses `kubelite`, so Longhorn's auto-detection misses the kubelet root path.
- Required fix: patch `longhorn-driver-deployer` command to include `--kubelet-root-dir /var/snap/microk8s/common/var/lib/kubelet`, then restart the deployer and verify CSI pods appear.

## Incident checkpoint - 2026-07-07 Longhorn post-patch verification pending

- User reports the MicroK8s kubelet-root patch appears to have helped Longhorn.
- Codex still cannot SSH to `main-server`, so direct cluster inspection is blocked by key auth.
- Public mini-app API remained healthy during verification: three probes to `/api/v1/models/catalog` returned HTTP 200.
- Next required output: Longhorn pod/daemonset/job/node/storageclass state to confirm CSI deployment completed and `longhorn-driver-deployer` stopped CrashLooping.

## Incident checkpoint - 2026-07-07 Longhorn validated

- Local `kubectl` context `microk8s` works from Codex, so cluster inspection no longer depends on SSH for Kubernetes API reads/writes.
- Longhorn is healthy after applying the MicroK8s kubelet-root patch: all Longhorn managers, engine images, instance managers, CSI controllers, and `longhorn-csi-plugin` pods are Running.
- Longhorn nodes are all Ready/Schedulable: `main-server`, `k8s-node-2`, and `k8s-node-3`.
- Patched installer-created `longhorn` StorageClass to non-default, leaving `microk8s-hostpath` as the only default StorageClass.
- Applied `k8s/longhorn-storageclasses.yaml`, creating explicit `longhorn-r2` and `longhorn-r3` StorageClasses with `Retain` reclaim policy.
- Smoke-tested `longhorn-r2` with a disposable 1Gi PVC and pod; pod mounted `/dev/longhorn/...`, wrote/read `longhorn-ok`, and Longhorn volume was `attached` and `healthy`.
- Cleaned up the smoke-test pod, PVC, released PV, and retained Longhorn volume. No Longhorn volumes remain after cleanup.
- Public mini-app API remained healthy after Longhorn validation.

## Incident checkpoint - 2026-07-07 PVC migration shortlist

- Built live PVC/workload map for Longhorn migration planning.
- Excluded databases and Nextcloud from first-pass migration candidates.
- Strong first test candidates: `affine/affine-config-pvc` and `affine/affine-storage` because Affine is scaled to 0; `default/ts3-storage` because it is small; TeamCity agent PVCs because they are build-agent state/cache and not production user traffic.
- Main-server disk relief candidates, in increasing risk: Affine app PVCs, Teamspeak PVC, TeamCity logs/data, registry claim, YouTrack PVC. Registry and YouTrack need backups/rollback planning before migration.
- Avoid first-pass migration for Postgres/CrunchyData PVCs, mini-app DB PVCs, CyberColors DB PVCs, Redis production PVCs, Remnawave DB, and Nextcloud PVCs.

## Incident checkpoint - 2026-07-07T19:10:30+01:00 Affine Longhorn PVC migration

- Migrated non-database Affine PVCs only: `affine-config-pvc` and `affine-storage` were copied to `affine-config-pvc-longhorn` and `affine-storage-longhorn`.
- Added `longhorn-r2-local` StorageClass with `numberOfReplicas=2`, `Retain`, ext4, and `dataLocality=best-effort` after the initial `longhorn-r2` target failed first mount for the 30Gi volume when both replicas were remote from `main-server`.
- Verified the prepare job mounted and formatted both Longhorn target PVCs on `main-server`; copy job copied 24Ki config and 15.5Mi storage.
- Patched `affine/deployment affine` to use the Longhorn PVCs, then scaled `postgres`, `redis`, and `affine` back to 1 replica.
- Verified Affine rolled out successfully, public `https://affine.kosh.games/` returned HTTP 200, and both migrated Longhorn volumes are healthy attached on `k8s-node-3`.
- Old Affine PVCs remain intact for rollback: `affine-config-pvc` and `affine-storage`; database PVC `pgdata-postgres-0` was not migrated.

## Incident checkpoint - 2026-07-07T20:05:00+01:00 TeamCity Longhorn PVC migration

- Migrated TeamCity server PVCs from `microk8s-hostpath` to Longhorn:
  - `gamedev/teamcity-data-pvc` -> `teamcity-data-pvc-longhorn`
  - `gamedev/teamcity-logs-pvc` -> `teamcity-logs-pvc-longhorn`
- Copied about 5.6Gi of TeamCity data and 101Mi of logs into the Longhorn target PVCs.
- Patched `gamedev/deployment teamcity-server-deployment` to mount the Longhorn PVCs and added node affinity excluding `main-server`.
- TeamCity server restarted on `k8s-node-3`; startup returned temporary 503 while TeamCity rebuilt `idea.zip` under `system/caches/agentTools`.
- TeamCity completed startup after about 15 minutes; public `https://teamcity.kosh.games/` now returns HTTP 401, TeamCity logs show `Server Started` and `Current stage: System is ready`, and both k8s-node agents re-registered.
- Longhorn reports both TeamCity volumes as `attached healthy` on `k8s-node-3`.
- Cleaned up one-off migration jobs `teamcity-longhorn-prepare` and `teamcity-copy-to-longhorn`.
- Old hostpath PVCs remain intact for rollback: `teamcity-data-pvc` and `teamcity-logs-pvc`.
- After the move, `kubectl top nodes` showed `main-server` memory around 9793Mi/61%, down from the earlier 65-72% range.

## Incident checkpoint - 2026-07-07T21:10:00+01:00 TeamCity third agent restored

- Scaled legacy `gamedev/deployment teamcity-agent-deployment` from 0 to 1 replica.
- The restored agent is pinned to `main-server` and uses distinct `AGENT_NAME=gamedev-teamcity-agent`, separate from the node2/node3 agents.
- Pod `teamcity-agent-deployment-68c4fd7fb4-ll4rm` is Running on `main-server`.
- Agent logs show Docker client/server/compose are available, plugin sync completed, and the agent registered with TeamCity as id `301`.
- TeamCity remained reachable; public `https://teamcity.kosh.games/` returned HTTP 401.
- Current TeamCity worker pods are running on all three nodes: legacy main-server agent plus `gamedev-teamcity-agent-k8s-node-2` and `gamedev-teamcity-agent-k8s-node-3`.
- After restoring the third agent, `kubectl top nodes` showed `main-server` around 10495Mi/66% memory.

## Incident checkpoint - 2026-07-07T21:25:00+01:00 YouTrack Longhorn PVC migration

- Migrated `gamedev/youtrack-pvc` from `main-storage` hostpath to `youtrack-pvc-longhorn` on Longhorn.
- Preserved RWX access mode because `gamedev/cronjob google-drive-backup` mounts the same PVC for Google Drive sync.
- Copied the full YouTrack PVC, including `data`, `conf`, `logs`, and `backups`; copy job reported 15.6G copied.
- Patched `gamedev/deployment youtrack-deployment` to use `youtrack-pvc-longhorn` and added node affinity excluding `main-server`.
- Patched `gamedev/cronjob google-drive-backup` to use `youtrack-pvc-longhorn`; temporarily suspended it during copy and resumed it after YouTrack was healthy.
- YouTrack restarted successfully on `k8s-node-3`; public `https://youtrack.kosh.games/` returned HTTP 200 and logs reached `Application state changed from APP_LIFECYCLE_START to RUNNING`.
- Longhorn reports volume `pvc-a9884e83-bda0-4e4b-88bf-44e4e150035d` as `attached healthy` on `k8s-node-3`.
- Cleaned up the one-off `youtrack-copy-to-longhorn` job.
- Old rollback PVC remains intact: `gamedev/youtrack-pvc` on `main-storage`.
- After the move, `kubectl top nodes` showed `main-server` around 9047Mi/56% memory, down from about 10385Mi/65% before the YouTrack migration.

## Incident checkpoint - 2026-07-08 old rollback PVC cleanup

- Verified migrated workloads still use Longhorn PVCs before deleting rollback storage:
  - `affine/deployment affine` uses `affine-storage-longhorn` and `affine-config-pvc-longhorn`.
  - `gamedev/deployment teamcity-server-deployment` uses `teamcity-data-pvc-longhorn` and `teamcity-logs-pvc-longhorn`.
  - `gamedev/deployment youtrack-deployment` uses `youtrack-pvc-longhorn`.
  - `gamedev/cronjob google-drive-backup` is resumed and uses `youtrack-pvc-longhorn`.
- Public health checks before cleanup: Affine returned HTTP 200, YouTrack returned HTTP 200, TeamCity returned expected unauthenticated HTTP 401.
- Deleted completed pre-migration Google Drive backup jobs `google-drive-backup-29721600` and `google-drive-backup-29723040` because their completed pods still referenced old `gamedev/youtrack-pvc`.
- Deleted old rollback PVCs:
  - `affine/affine-config-pvc`
  - `affine/affine-storage`
  - `gamedev/teamcity-data-pvc`
  - `gamedev/teamcity-logs-pvc`
  - `gamedev/youtrack-pvc`
- Deleted remaining released hostpath PVs for old TeamCity data and old YouTrack; the other old hostpath PVs had already disappeared by verification time.
- Verified only the replacement Longhorn PVs remain for Affine, TeamCity server, and YouTrack, and all corresponding Longhorn volumes are `attached healthy`.
- Temporary node debug check on `main-server` after cleanup showed root `/` at 364G used / 119G free (76%), `/mnt/second_drive` at 216G used / 251G free (47%), and `/var/lib/longhorn` at 18G used / 262G free (7%).
- Deleted the temporary `node-debugger-main-server-v9c8w` pod.
- One unrelated issue remains: `cybercolors-indexer-worker-7f64c66b4-zqbq4` is Pending and was not part of this PVC cleanup.

## Incident checkpoint - 2026-07-08 CyberColors indexer pending pod fix

- The pending `cybercolors-indexer-worker-7f64c66b4-zqbq4` was not caused by replica count; the Deployment already desired 1 replica.
- The rollout was stuck with 2 total pods because the new template had contradictory scheduling rules: `nodeSelector` pinned it to `main-server`, while required node affinity allowed only `k8s-node-2` and `k8s-node-3`.
- Removed the bad `spec.template.spec.affinity` from `cybercolors/deployment cybercolors-indexer-worker`, matching the last-applied manifest intent of one worker pinned to `main-server`.
- Rollout completed successfully; final state is `cybercolors-indexer-worker` 1/1 with pod `cybercolors-indexer-worker-7fd85b7fbd-477vc` Running on `main-server`.
- `kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded` returned no resources afterward.

## Incident checkpoint - 2026-07-08 CyberColors indexer moved off main-server

- User correctly challenged the earlier live-only fix: the CyberColors indexer worker should not remain on `main-server` after the load-balancing work.
- Root cause was the GitOps source manifest in `cybercolors_bot/deploy/k8s/cybercolors/workers.yaml`, which pinned `cybercolors-indexer-worker` with `nodeSelector: kubernetes.io/hostname=main-server`.
- A live patch to require `k8s-node-2`/`k8s-node-3` was immediately reverted by ArgoCD self-heal because the Argo app `cybercolors` tracks `https://github.com/l1ghtny/cybercolors_bot.git`, path `deploy/k8s/cybercolors`, target `master`.
- Updated the GitOps source manifest to remove the `main-server` nodeSelector and add required node affinity for `k8s-node-2` or `k8s-node-3`.
- Committed and pushed CyberColors repo commit `b3a74e6` (`Move CyberColors indexer worker off main server`) to `origin/master`.
- Refreshed ArgoCD app `cybercolors`; app synced to revision `b3a74e6141020958de99429ef117dafadd81bcbd` and reported Healthy.
- Final worker state: exactly one pod, `cybercolors-indexer-worker-84f5876d6d-6xpxl`, Running on `k8s-node-3`; no non-running pods remained cluster-wide.
- Final node memory after move: `main-server` around 8312Mi/52%, `k8s-node-2` around 6819Mi/44%, `k8s-node-3` around 9659Mi/62%.


## 2026-07-10 GPT-5.6 tier upgrade

- Upgraded the active OpenAI paid tiers while keeping the Fast tier on `gpt-5.4-nano`:
  - Smart: `gpt-5.4-mini` -> `gpt-5.6-luna`
  - Balanced: `gpt-5.4` -> `gpt-5.6-terra`
  - Flagship: `gpt-5.5` -> `gpt-5.6-sol`
- Added backend legacy-id canonicalization, shared usage buckets, provider/display mappings, and premium-sample updates.
- Added migration `n1a2b3c4d5e6_upgrade_openai_text_models_to_gpt_5_6.py` for user/conversation defaults, entitlement limits, catalog metadata, and standard API pricing.
- Preserved historical request/token model ids while clearing OpenAI chain state for migrated conversations.
- Added GPT-5.6 reasoning efforts `none`, `low`, `medium`, `high`, `xhigh`, and `max`; reasoning-off now sends explicit `none`.
- Added hashed OpenAI `safety_identifier` values and split reasoning tokens from total output tokens to prevent double charging.
- Updated frontend model-id normalization, labels, premium sample copy, and catalog capability types.

### Verification

- Backend targeted tests: 26 passed.
- Backend Python compilation: passed.
- Alembic heads: `n1a2b3c4d5e6 (head)`.
- Frontend Vitest: 3 passed.
- Frontend production build: passed with existing chunk-size/dynamic-import warnings.

### Next steps

- Apply `poetry run alembic upgrade head` in the target environment during the backend rollout.
- Canary one send per tier and verify model id, SSE completion, reasoning-off behavior, entitlement decrement, and recorded cost before promotion.

## 2026-07-26 Browser web app foundation

- Corrected the `AppUser` model invariant after review: internal `id` is typed as a mandatory `uuid.UUID` and remains a non-null primary key; only `telegram_id` is nullable for browser-only accounts.
- Added provider-neutral `UserIdentity` records and nullable Telegram identities so browser-only users can share the existing conversation, entitlement, ledger, and SSE backend.
- Added single-use, hashed, expiring email magic-link challenges with email/IP rate limiting, SMTP delivery, `/auth/me`, and optional email-to-Telegram account linking.
- Added the Alembic migration and Telegram identity backfill; Telegram broadcasts and Telegram-only image sharing now explicitly exclude or reject web-only users.
- Replaced wildcard CORS with an environment-configurable origin allowlist while preserving the existing local, preview, and production origins.
- Updated the React frontend to authenticate automatically in Telegram and show passwordless email login in a normal browser when `VITE_WEB_AUTH_ENABLED=true`.
- Added callback verification, authenticated session hydration, browser logout, browser-safe sharing, and an account-settings flow for linking email to an existing Telegram account.
- Added English and native-Russian web-auth UI copy plus Docker/Vite build configuration.

### Verification

- Backend web-auth tests without external PostgreSQL: 1 passed, 2 database integration tests skipped because `TEST_DATABASE_URL` is not configured.
- Backend Python compilation and targeted Ruff checks passed.
- Alembic reports `o1a2b3c4d5e6` as the single head; offline upgrade SQL generation passed.
- Frontend ESLint and TypeScript checks passed.
- Frontend Vitest: 3 passed.
- Frontend production build passed with the repository's existing chunk-size and mixed dynamic/static import warnings.

### Next steps

- Run the two database integration tests against a disposable PostgreSQL database.
- Configure backend `WEB_AUTH_ENABLED`, callback URL, SMTP credentials, and the browser origin allowlist; configure frontend `VITE_WEB_AUTH_ENABLED` and `VITE_WEB_APP_URL`.
- Apply `poetry run alembic upgrade head`, deploy backend first, then deploy frontend and canary email request, link consumption, account linking, chat send/SSE resume, billing, and logout.
- Add challenge cleanup/retention and migrate bearer-token storage to secure HttpOnly cookies before treating browser authentication as fully hardened.

## 2026-07-31 lightny.ru browser rollout checkpoint

### Objective

- Release the accumulated backend/frontend browser-pilot work through TeamCity and Argo Rollouts without disrupting Telegram Mini App users.

### Completed

- Added `app.lightny.ru -> 193.233.251.127` in Timeweb DNS and issued an unattended Let's Encrypt certificate through the SPB nginx server.
- Added dedicated SPB nginx virtual hosts for app, API, and image traffic; fixed TLS session reuse across the shared multi-SNI MicroK8s ingress; verified frontend and representative FastAPI routes through `https://app.lightny.ru`.
- Renewed the `api.lightny.ru` + `images.lightny.ru` certificate with the nginx authenticator and disabled only the unused manual `www.lightny.ru` renewal file recoverably.
- Added explicit one-pod, zero-weight canary scale steps to backend and frontend Rollouts while preserving Argo CD ownership annotations; cleared stale scaled-down candidates and applied the strategies live.
- Patched production `backend-env` for `app.lightny.ru`, secure cookies, passkey RP/origin, browser auth, Telegram OIDC disabled, and the new image base URL. Existing stable pods were not restarted.
- Moved the frontend Sentry build credential out of the TeamCity script into a masked environment parameter. TeamCity now builds for `app.lightny.ru`, enables browser/passkey auth, and hides unconfigured Telegram OIDC and email methods.
- Completed a fresh pgBackRest differential backup job before migration.
- Frontend validation: 23 tests passed, TypeScript passed, production build passed, and the passkey-only login screen was visually checked in a local browser.
- Converted three legacy CP1252 Python test files to UTF-8 so pytest collection works on Python 3.14. Backend compilation and Alembic offline SQL from `n1a2b3c4d5e6` to `w1a2b3c4d5e6` passed.
- Backend full suite passed against the disposable PostgreSQL database and live Redis: 211 passed, 2 skipped, 6 warnings in 28m26s. Focused Ruff checks for all touched Python files and `git diff --check` also passed.

### In progress

- Commit and push the two validated release candidates, then deploy through TeamCity and Argo Rollouts.

### Next steps

- Commit both repositories and push backend `master` plus frontend `main`.
- Monitor TeamCity build/migration/deploy jobs, validate zero-weight canaries, run real app/chat/SSE checks, and promote only after evidence is clean.
- Have the user confirm the production WebAuthn biometric ceremony; automated checks can validate only the surrounding API/browser flow.
- Before a broad public rollout, separately approve and deploy a source-IP-preserving ingress path; the current MicroK8s host-port path rewrites the edge peer to loopback, so ingress cannot safely recover each public client's address.

## 2026-07-31 lightny.ru marketing-site rollout

### Completed

- Pushed `chat-search-link` through commit `3b713723e9557d5854d91d9bcd3ece40a078579a` with a production Dockerfile, MicroK8s deployment/service/ingress, and TeamCity pipeline source.
- Created TeamCity build configuration `TelegramMiniAppProject_LightnyWebsiteDeploy` with a private GitHub VCS root backed by a repository-scoped read-only deploy key.
- TeamCity build `5860` built and pushed `lightny-website:2`, deployed two ready pods across nodes 2 and 3, and passed in-pod checks for `/`, `/privacy`, `/terms`, `/account-and-data`, and `/sitemap.xml`.
- Changed only the SPB nginx `location /` upstream to `http://10.77.0.1:80` with `Host: lightny.ru`; preserved API, image, WebSocket, and XHTTP routes. Rollback copy: `/etc/nginx/sites-available/lightny.ru.pre-marketing-20260731-1102`.
- Public verification passed for the marketing and legal routes, sitemap/robots, canonical host, browser-app CTAs, and representative `/api/v1/models/catalog`; desktop and 390x844 browser structure/menu checks passed with no console errors.

### Next steps

- Automatic production deployment is now explicitly approved and enabled: TeamCity has one `vcsTrigger` for the default branch with queue optimization, and enabling it did not queue an extra build.
- SEO/conversion audit found three release-priority gaps before paid acquisition: Russian browser locales such as `ru-RU` fall back to English on the app login screen; login copy mentions email when production email auth is hidden; and SEO task links preserve a prompt but do not activate the corresponding search/document/image workflow.
- Add real cross-domain funnel measurement before changing copy: SEO CTA click -> app landing -> auth success -> first completed message -> subscription, carrying `source` and `entry` throughout.
- Submit `https://lightny.ru/sitemap.xml` to Yandex Webmaster and Google Search Console; the new domain currently has no visible `site:lightny.ru` results.

## 2026-07-31 browser brand and email copy follow-up

### Completed

- Changed the default passkey relying-party display name and passkey fallback user label to Lightny AI.
- Changed the passwordless login email subject to Lightny AI while preserving the explicit on-page confirmation safety step.

### Verification

- Ruff passed for the changed backend files.
- Focused PostgreSQL auth/passkey suite passed: 16 tests.

### Next steps

- Deploy the backend copy/config defaults before or together with the coordinated frontend and SEO release.

## 2026-07-31 account language and Telegram profile photo

### Completed

- Added an account-level `preferred_language` preference with `en`/`ru` validation and user-settings API support.
- Persisted validated Mini App `photo_url` and Telegram OIDC `picture` claims as `telegram_photo_url`.
- Returned saved language and Telegram photo through every `/auth/me`-style profile response, including passkey and browser-session restores.
- Added migration `y1a2b3c4d5e6` after the account-onboarding migration.

### Verification

- Ruff passed for the complete changed backend surface.
- The final 23-test auth, passkey, Telegram OIDC, user-settings, identity-linking, profile, and message release matrix passed against `TEST_DATABASE_URL`.

### Next steps

- Deploy migration `y1a2b3c4d5e6` before the frontend that sends the account language field.
- Existing users receive their Telegram photo on their next Mini App or Telegram OIDC authentication; the initials fallback remains for users without a shared photo.

## 2026-07-31 coordinated production release

### Completed

- Pushed backend feature commit `ae85312` to `master`.
- TeamCity backend build `5871` (`#267`) succeeded for that exact revision.
- TeamCity migration run `5875` (`#139`) reused build `5871`, upgraded the production database successfully, and deployed backend image `localhost:32000/tg-mini-app-backend:267`.

### In progress

- Persist backend image `267` in GitOps, validate the zero-weight canary, and promote it before releasing the matching frontend.

### Next steps

- Verify backend canary readiness, authenticated/public API behavior, and logs, then promote the Rollout fully.
- Release and promote the matching frontend, then deploy the consented attribution update to `lightny.ru`.
- Confirm the production WebAuthn biometric ceremony manually after the frontend rollout.

## 2026-08-01 dedicated AWG ingress and client identity

### Completed

- Deployed a dedicated ingress-nginx DaemonSet with no `hostNetwork` or
  `hostPort`; the existing `30445` NodePort now selects those pods with
  `externalTrafficPolicy: Local`.
- Restricted controller HTTPS to the SPB AWG peer with a NetworkPolicy; direct
  public access to node port `30445` now times out while SPB/AWG returns 200.
- Scoped the controller to the `gpt` namespace through a namespace selector and
  dedicated RBAC while leaving Argo-owned stable/canary Ingress objects intact.
- Restricted ingress real-IP trust to the exact SPB AWG peer `10.77.0.2/32` and
  updated backend proxy trust to `10.1.0.0/16,10.77.0.2/32`.
- Fixed the SPB Nginx trust boundary to overwrite client-supplied forwarding
  headers in every proxied location; the first adversarial test exposed an
  Nginx header-inheritance bug, and the repeated test rejected both forged IPs.
- Confirmed two real network origins created two distinct hashed Redis passkey
  rate-limit identities; forged identities created no keys. Removed the exact
  rate-limit keys generated by the audit.
- Backend image `267` was restarted safely after the trusted-proxy update and is
  Healthy with two Ready replicas.

### Release completed

- Passkey limits now use global (2,000/hour), account (30/hour), one-time
  challenge (3 within the challenge TTL), and a higher 120/hour secondary IP
  guard.
- Focused auth/passkey tests pass (`12 passed`); Ruff, formatting, Python
  compilation, Kubernetes server-side dry-run, and SPB `nginx -t` pass.
- Commit `9081186` built successfully as TeamCity `5912/#269`; GitOps commit
  `5dd9ba2` was promoted through a zero-weight canary. Argo is Synced/Healthy
  with exactly two Ready image-269 pods.
- Stable public health, passkey challenge issuance, frontend loading, and
  browser login rendering pass. The browser console has no errors; only the
  pre-existing Telegram WebApp version warnings remain.

### Rollback

- Kubernetes manifests were captured in
  `/private/tmp/lightny-awg-ingress-backup.tinpv1` before the switch.
- SPB Nginx site backups are in
  `/root/lightny-nginx-backup-20260801T1115Z` on the SPB server.
- Restore the NodePort selector to `name: nginx-ingress-microk8s` to return to
  the prior hostPort path.

### Next steps

1. Retain the Kubernetes and SPB Nginx rollback copies through the first public
   traffic window.
2. Monitor passkey 429 rates and dedicated ingress errors after release; no
   further rollout action is required for this change.

## 2026-08-02 unified release flow and shared-history beta

### Completed

- Added a versioned TeamCity pipeline definition that builds backend and frontend images in parallel, verifies the backend image before migration, and deploys the API, frontend, conversation-search worker, bot-command worker, and both backend CronJobs.
- Replaced indefinite production canary pauses and named-user header routing with automatic 0% readiness analysis, timed 20% and 100% stages, automatic completion, and deadline aborts.
- Added deterministic backend and frontend canary health probes; the frontend now publishes `/health.json`.
- Added registry preflight and immutable image verification to prevent migration Jobs from being created for missing tags.
- Documented the `beta.app.lightny.ru` shared-history architecture and phased implementation plan.
- Created `LightnyReleaseFlow` in TeamCity with both private repositories attached and enabled its default-branch change trigger.
- TeamCity run `#4` completed all four jobs successfully. It built and migrated release `4`, then automatically promoted backend and frontend through all three Argo readiness gates without manual intervention.
- Argo CD is Synced/Healthy on backend image `4` and frontend image `4`; both Rollouts are Healthy with their current revisions marked stable. The conversation-search worker, bot-command worker, cleanup CronJob, and subscription CronJob use backend image `4`.
- The bot-command worker exposed a pre-existing 256 MiB memory ceiling during post-release verification. Its limit was raised live to 512 MiB, after which the replacement pod became Ready with zero restarts; `deploy_stack.sh` now preserves that limit on future releases.
- Disabled legacy TeamCity VCS triggers `TRIGGER_7` on `MiniAppTgGpt_BuildBackend` and `TRIGGER_8` on `TelegramMiniAppProject_TgMiniFrontendNewUI_Build`; the replacement pipeline is now the only automatic release path for either repository's default branch.

### Risks / blockers

- No activation blocker remains. The first default-branch-triggered run still needs runtime verification before this migration is considered closed.

### Next steps

1. Commit and push the persisted 512 MiB bot-worker limit, then confirm the new default-branch trigger starts exactly one `LightnyReleaseFlow` run and the legacy builds remain idle.
2. Runtime-verify that triggered run, including both Rollouts, all workers, both CronJobs, and public health endpoints.
3. Implement beta Phase 1 guardrails before provisioning shared-production-data beta workloads.
## 2026-08-15 Work activity schema expansion

- Added the empty `work_run_activity_event` table through an additive migration
  on the production release branch before beta code begins reading or writing it.
- No existing table, column, index, constraint, or row is changed.
- This deliberately keeps the beta lane's shared-database guard intact: beta
  verifies the production schema head and never runs migrations itself.

### Next

1. Complete the production migration release.
2. Rerun the coordinated beta Work activity timeline release and verify it live.
