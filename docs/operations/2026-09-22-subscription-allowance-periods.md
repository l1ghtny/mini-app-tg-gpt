# Subscription-aligned allowance periods

Status: live and verified on production and beta at 2026-09-22 12:29 UTC. The shared gate is subscription; do not restore calendar-only writers.

## Behaviour

- A private invitation lasting at most 31 days receives one grant from activation until access expires. A 30-day code redeemed on 22 September at 15:00 UTC therefore lasts until 22 October at 15:00 UTC, without an October 1 refill.
- Ongoing private access receives anniversary-month grants anchored to subscription activation, with a partial final period capped at access expiry. Month-end anniversaries use the original day: January 31, February 28/29, March 31. UTC timestamps retain the activation time.
- Overlapping private subscriptions share a cycle and use the highest active capacity. Changing tiers preserves spent units and reservations. Early extensions do not refill the currently open allowance. Reactivation after a gap starts a new cycle.
- Starter trial lifetime, activation on first success, and seven-day expiry are unchanged. The platform-wide supplier spending guard deliberately remains a calendar-month budget.
- This applies to the existing shared-allowance private subscription paths. Public paid-plan purchases and their payment integration remain a separate unfinished release scope; this change does not enable them.

## Ledger transition

Migration `xw0e1f2a3b4d`, after the passkey migration `xw0e1f2a3b4c`, adds nullable `allowance_account.subscription_anchor` and the singleton `allowance_control` row, initially in `calendar` mode. It is repeat-safe and retains financial metadata on downgrade; retries never overwrite the current gate mode. No migration creates a grant or resets a balance.

On first access, one overlapping legacy calendar account is aligned in place. Its ID, request links, spend, pending reservations, Luna balance and provider attempts remain unchanged. A zero-unit `period_alignment` event records old and new boundaries in its event key. This also preserves usage when the first post-release access happens after the calendar month changes. Multiple overlapping legacy accounts fail closed with `allowance_period_reconciliation_required`, requiring audited reconciliation rather than silently discarding spend or granting another balance.

## Frontend contract and paired implementation

The allowance snapshot adds `period_ends_at`, `period_end_kind` (`reset`, `access_expiry`, `trial_expiry`) and `access_expires_at`. `resets_at` is null when access ends without another grant. Trial nullability remains supported.

Paired frontend worktree: `/private/tmp/lightny-subscription-periods-frontend`, same branch name. Synced with the passkey task's production commits: backend `b3746a8`, frontend `4563fed`. Its releases 9088/#70 and 9093/#185 completed before this release began.

- `src/lib/allowance.ts`: optional typed period metadata, compatible with old responses.
- `src/components/AllowancePlans.tsx`: distinguish “Allowance resets on” from “Access ends on”; never format null as Invalid Date; remove unconditional renewal promises from exhaustion guidance.
- `src/components/AllowancePlans.test.tsx`: fixed-term, renewal, null-date and Russian display coverage.

Deploy the matching frontend before switching backend semantics: the previous component formats a null non-trial reset as Invalid Date. No SSE, model selection, document, code-redemption or authentication contract changes.

## Validation

- 44 isolated-schema backend tests passed across subscription periods, private tiers, code redemption, starter trials and accounting; two additional migration/cutover-safety tests passed (46 total).
- Checks include first-of-month continuity, expiry without refill, month-end/leap-year boundaries, tier overlap, downgrade, early extension, reactivation, concurrency, in-flight settlement into the original account, beta/production shared accounting, and repeat-safe migration with spend preservation.
- Frontend: 13 tests, TypeScript, focused ESLint, and production build passed. Build retains existing chunk-size/dynamic-import warnings and requires deployment environment configuration at release.
- Actual AllowanceMeter reviewed in local preview at 1280px desktop and 390px mobile, including English reset and Russian expiry states. Temporary preview files are untracked and must not ship.
- Ruff and `git diff --check` passed. No root pytest fixture or production database was used.
- After syncing the passkey release: four focused backend period/migration tests, 18 frontend allowance/passkey tests, TypeScript and single-head/offline migration checks passed.

## Coordinated release

1. Rebase/merge current production changes and ensure one Alembic head, including any concurrent passkey migration. Carry identical migration history to beta.
2. Read-only preflight: inspect current active subscriptions and overlapping allowance periods, including both shared ledger scope users and recently redeemed invitations. Resolve ambiguous historical balances explicitly before rollout; do not reset them.
3. Apply the additive schema migration and release both frontend/backend pairs. The gate remains `calendar` throughout canary rollout: updated and old processes use the same accounting semantics. Ensure the compatible frontend is live before enabling subscription semantics.
4. Verify exact revisions of EVERY live API/worker/bot and scheduled-job template on both channels, with no old writer pods remaining. Run `python scripts/release/allowance_period_cutover.py status` inside an updated backend. Do not pause until this verification completes.
5. Run the operator's `pause` command. Every allowance account lookup takes a shared transaction lock on the gate row; the operator takes an exclusive lock. This waits for admitted account transactions and then durably blocks all new allowance-writing reads/admissions on both channels with 503/Retry-After. Existing requests can continue and settle without that gate.
6. Poll `status` until active requests, shared holds and Luna holds are zero. Bound draining at five minutes. If it does not drain, use `resume-calendar` (allowed only while no accounts are aligned) and investigate; do not fail requests or erase holds to force deployment through.
7. Run `enable`. Under the exclusive gate lock, this checks that draining is complete, aligns existing accounts without creating new grants, verifies every financial balance is unchanged, and changes the mode to `subscription` in the same commit. Failed checks roll back, leaving the pause in place for safe retry or resume-calendar. Repeated enable is idempotent.
8. Verify exact deployed revisions, unchanged spent/reserved totals, anniversary/expiry dates and authenticated UI on both domains. Verify expiry rejection and in-flight settlement in isolated tests; do not modify a real subscription to manufacture this condition.
9. Application rollback after enable must retain the subscription-period selector or pause admissions. The operator rejects resume-calendar after any alignment. Never restore an old DB snapshot or drop period provenance.

## What's New gate

What's New: required. Users will see changed refill dates and a distinction between access expiry and allowance reset. After live acceptance, the prepared note was promoted into migration `xw0e1f2a3b4e`, following `xw0e1f2a3b4d`. Publish via the normal production migration pipeline and carry identical history to beta.

- Stable proposed ID: `2026-09-22-subscription-allowance-periods`.
- English title: “Allowance dates follow your subscription”. Body: “Your allowance now follows your subscription period. A 30-day invitation includes one allowance for those 30 days. In Subscription, you can see when your allowance resets or your access ends.”
- Russian title: «Даты обновления лимита привязаны к подписке». Body: «Лимит теперь действует в течение периода подписки. Приглашение на 30 дней даёт один лимит на весь этот срок. В разделе «Подписка» указано, когда лимит обновится или закончится доступ.»

## Live release acceptance

- Production 9098/#71 and beta 9103/#186 passed all four TeamCity stages. Child build revisions: production backend `01222fb149e0fdb0090daff45121ab4d34a5d193`, frontend `ea4a89bdfc48641bf946754b86757a5cb7cfad98`; beta backend `1117693502fd4d00c90d1de6c4cf62f119b0af0f`, frontend `c09fe90365446afae6c61ae43ea41176003fc92a`.
- Both production Rollouts Healthy; all live APIs, frontends, workers and bot use 71 / beta-186, and both application CronJob templates use 71 with no active old jobs. Public production readiness and beta local readiness passed with database and Redis OK.
- At 12:29:11 UTC admissions paused. Zero active requests and zero holds. At 12:29:37 UTC the atomic operator aligned 12 private accounts and enabled subscription mode. All 13 account IDs and financial balances preserved: shared spent 624432, Luna spent 5110, no reservations. No grants, resets, user creations or redemption fixtures.
- Authenticated checks through app.lightnyai.ru and beta.app.lightny.ru returned identical existing Smooth allowance and September 29 anniversary. Existing browser sessions on production and beta both display the other Smooth account at unchanged 96.7% with its actual October 4 reset, replacing October 1.
- Expiry without refill and in-flight settlement were verified in isolated tests, not by changing live subscriptions. Paid checkout remains outside this release.
- Follow-up announcement migration xw0e1f2a3b4e is published only after the above live acceptance.
