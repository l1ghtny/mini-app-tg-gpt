# Starter trial and contextual onboarding

Implemented locally on paired `codex/starter-onboarding-20260921` branches. No production data, environment flags, or deployed images have been changed for this work.

## Approved policy

- One grant per account: 500,000 shared accounting units (40% of Start) and 100,000 Luna units. All seven chat models, Flare, automatic web search, and document search are available. Sonnet is the initial default when no supported model preference exists.
- Seven consecutive days begin only after the first successful, persisted answer/image. Login, estimates, uploads, failures and cancellations do not activate the clock. Failed Luna calls are also platform-funded.
- The grant does not renew. An account lock and partial unique index prevent concurrent or cross-environment duplicate trials. A fixed internal period key identifies the lifetime grant; API responses expose nullable activation/expiry dates, never the internal sentinel dates.
- Private subscriptions retain their approved Premium/Max monthly capacities. On enabling the trial flag, other accounts (including legacy Welcoming Bonus users) receive a fresh one-time trial; historical successes do not backdate activation. Legacy subscription records are retained for rollback, but their old counters do not stack with shared usage.
- Expiry blocks new generation and document uploads. Existing results remain readable under the existing content-retention policies. Document trial capacity is five files, 25 MiB total, 10 MiB per file, no pinning, seven-day document retention. These limits are returned through the existing document capability UI.

## Presentation and discovery

The current-plan section separates allowance from a seven-segment clock: Ready, Active, Expired. The pending state explains the first-result trigger. Exact expiry, no automatic renewal, and optional trial terms are available without an opening modal. The last 24 hours get a status label; low allowance is shown in the plan section. Exhaustion opens this section and preserves the draft/attachments.

No automatic welcome/coachmark tour or legacy credit toast. Task starters are unchanged. Search stays available automatically through the existing `auto` tool default, including current-provider task presets; no search tutorial is added. Existing document readiness, model-purpose/usage labels, and image quality controls remain contextual. Generated images gain an Edit image action that attaches that image to the owning chat composer without overwriting the draft. Composer placeholders switch to the next useful action when documents or image-edit references are attached.

Suggestions use server-counted completed allowance requests, not sends or empty chats:
- Personalisation: three successes in at least two chats, no existing profile, on the next idle empty composer.
- Projects: three successful chats and at least three existing chats, no project; action beside the chat list.
- Installation: three sessions spanning at least two UTC days and a successful task; only supported browser/iOS install surfaces, never already installed or inside Telegram.
- One suggestion per 24 hours across devices; each v2 suggestion is shown at most once, and dismissal persists on the account. Hidden surfaces, active drafts, streaming, uploads, dialogs, offline state and error alerts suppress presentation. Optional suggestions never block sending.
- Account-scoped session IDs plus a 30-minute server deduplication window keep refreshes from inflating visits. Progress telemetry reports successful tasks, chats, sessions and active days without chat content.

## Rollout

1. Apply additive migration `xu8c9d0e1f2a` through the normal migration job. It is repeat-safe; rollback does not delete financial activation history.
2. Deploy the paired backend/frontend changes to beta first. Keep production on its current settings during acceptance.
3. Explicitly enable `SHARED_ALLOWANCE_TRIAL_ENABLED=true` alongside `SHARED_ALLOWANCE_ENABLED=true` for the approved environment/cohort. The trial switch defaults **off**. Beta's existing account allowlist still applies; do not bypass it to test signup. Use a dedicated authorised non-private test account; existing private accounts correctly keep private capacity.
4. Keep the existing shared `SHARED_ALLOWANCE_SCOPE` consistent across beta/production. Verify the aggregate provider-spend guard against planned trial acquisition; it remains in force and is not a promise of a $0.60 invoice ceiling.
5. Verify ready state before a request, failed request without activation, persisted success with a seven-day expiry, another login without refill, image edit, document search, low/exhausted allowance and expiry with the draft preserved.
6. Public paid-plan purchases remain disabled in the existing shared-allowance catalog. Complete the paid conversion/checkout path before opening this trial as a public acquisition funnel. This onboarding change does not implement payment activation.

Frontend contract additions: `mode: trial`, nullable `resets_at`, `trial` lifecycle metadata and nullable history period dates; account onboarding visit/claim endpoints and v2 state IDs. Both sides must ship together. The original user workspaces and existing unrelated changes are preserved.

## Access-code restoration

Plans now includes a compact expandable promo/access-code field. Accepted codes refresh the allowance and model catalog; active discounts remain visible with eligible plans and expiry. Refresh failure is distinct from redemption failure and retries only reads. Code entry is account-scoped and blocks concurrent submits.

With the trial switch enabled, active supported private subscriptions authorize their mapped allowance without requiring membership in the historical production rollout list. Beta's existing access allowlist still applies. The existing cohort gate remains unchanged when trials are disabled. Newly issued code subscriptions use UTC activation timestamps; expired active memberships can be reactivated. Reapplying an active discount does not duplicate it or consume another code use. Legacy packs and unsupported tier grants are rejected before code consumption for shared-allowance accounts.

Validation: frontend full suite 347 passed, mirrored beta component tests 6 passed, TypeScript in both variants, focused lint and production build passed. Backend 62 unchanged cases passed in the full run; all four new authenticated code-redemption cases passed after the UTC fix (64 distinct cases verified). Mobile and desktop code-field layouts reviewed in EN/RU. All test data lives in isolated schemas; no live codes redeemed.

Remaining release gates: commit/review the paired changes excluding preview fixtures; apply the additive onboarding migration and deploy beta; enable the trial flag for the approved beta cohort and verify fresh-account login, first successful request, code redemption, updated models/documents, trial exhaustion and expiry. Before public acquisition, complete paid checkout and reconcile existing discount tier mappings with the new catalog. Sharing redesign is a separate deferred task.
