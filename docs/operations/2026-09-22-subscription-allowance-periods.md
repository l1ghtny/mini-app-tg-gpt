# Subscription-aligned allowance periods

Status: implemented and tested on `codex/subscription-allowance-periods-20260922`. Feature-branch publication is authorized; production/beta merges and deployment are held while the passkey/settings task completes its paired release. No live allowance changes.

## Behaviour

- A private invitation lasting at most 31 days receives one grant from activation until access expires. A 30-day code redeemed on 22 September at 15:00 UTC therefore lasts until 22 October at 15:00 UTC, without an October 1 refill.
- Ongoing private access receives anniversary-month grants anchored to subscription activation, with a partial final period capped at access expiry. Month-end anniversaries use the original day: January 31, February 28/29, March 31. UTC timestamps retain the activation time.
- Overlapping private subscriptions share a cycle and use the highest active capacity. Changing tiers preserves spent units and reservations. Early extensions do not refill the currently open allowance. Reactivation after a gap starts a new cycle.
- Starter trial lifetime, activation on first success, and seven-day expiry are unchanged. The platform-wide supplier spending guard deliberately remains a calendar-month budget.
- This applies to the existing shared-allowance private subscription paths. Public paid-plan purchases and their payment integration remain a separate unfinished release scope; this change does not enable them.

## Ledger transition

Migration `xw0e1f2a3b4d`, after the passkey migration `xw0e1f2a3b4c`, adds nullable `allowance_account.subscription_anchor`. It is repeat-safe and retains the column on downgrade. No migration creates a grant or resets a balance.

On first access, one overlapping legacy calendar account is aligned in place. Its ID, request links, spend, pending reservations, Luna balance and provider attempts remain unchanged. A zero-unit `period_alignment` event records old and new boundaries in its event key. This also preserves usage when the first post-release access happens after the calendar month changes. Multiple overlapping legacy accounts fail closed with `allowance_period_reconciliation_required`, requiring audited reconciliation rather than silently discarding spend or granting another balance.

## Frontend contract and paired implementation

The allowance snapshot adds `period_ends_at`, `period_end_kind` (`reset`, `access_expiry`, `trial_expiry`) and `access_expires_at`. `resets_at` is null when access ends without another grant. Trial nullability remains supported.

Paired frontend worktree: `/private/tmp/lightny-subscription-periods-frontend`, same branch name. Synced with the passkey task's production commits: backend `b3746a8`, frontend `4563fed`. The passkey task owns release 9088/#70 and its subsequent beta release; this task must not modify or overlap them.

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
3. Deploy the backward-compatible frontend. Apply the additive schema migration.
4. Briefly quiesce generation admissions and allowance-writing reads across BOTH production and beta. Drain existing requests and roll both backends/workers to the new version before reopening. Never run calendar-account writers concurrently with subscription-account writers against the same scope: an old instance can recreate a calendar grant after alignment. Plan this cutover with the existing release controls; no infrastructure changes are part of this patch.
5. Verify exact deployed revisions, unchanged spent/reserved totals, anniversary/expiry dates and authenticated UI on both domains. Verify admission after expiry is denied and in-flight accounting still settles.
6. Application rollback after alignment must keep the subscription-period account selector or keep shared allowance admissions paused. Do not restore an old DB snapshot, drop period provenance or resume calendar-grant writers against aligned accounts.

## What's New gate

What's New: required. Users will see changed refill dates and a distinction between access expiry and allowance reset. The idempotent data migration is prepared in `docs/operations/pending_subscription_periods_announcement.py`, outside the active Alembic path. Assign its revision/parent and promote it only after production is verified; do not insert planned content into the shared feed or bundle it into the schema migration that runs first.

- Stable proposed ID: `2026-09-22-subscription-allowance-periods`.
- English title: “Allowance dates follow your subscription”. Body: “Your allowance now follows your subscription period. A 30-day invitation includes one allowance for those 30 days. In Subscription, you can see when your allowance resets or your access ends.”
- Russian title: «Даты обновления лимита привязаны к подписке». Body: «Лимит теперь действует в течение периода подписки. Приглашение на 30 дней даёт один лимит на весь этот срок. В разделе «Подписка» указано, когда лимит обновится или закончится доступ.»
