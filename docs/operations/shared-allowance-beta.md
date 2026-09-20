# Shared allowance beta rollout

Implementation date: 20 September 2026. This replaces the expired 19 September
delivery target in the product plan. Code and local acceptance are separate from
a beta deployment. No beta or production deployment was performed during this
implementation.

## Paired revisions and migration order

Backend and frontend feature branches are `codex/shared-allowance-20260920`, based
on backend `master` (`280b5d3`) and frontend `main` (`9d459e6`). Keep unrelated beta
Work changes out of those production-based branches.

The additive migration `xt7b8c9d0e1f` follows `xs6a7b8c9d0e`. It creates allowance
accounts, requests, events and provider attempts. It does not change existing
tiers, subscriptions, histories or balances. Upgrade is repeat-safe. Downgrade
deletes the new accounting tables, so use the feature flag to roll back behavior
after usage exists; do not downgrade live accounting.

The live TeamCity pipeline `TelegramMiniAppProject_LightnyBetaFlow` was inspected
on 20 September. Its shared-schema job uses `MIGRATION_MODE=check`, not upgrade.
Land and apply the additive migration through the production schema-owner path
before deploying a beta image with this Alembic head. Keep the feature disabled
on production. Do not change the beta schema job to bypass that ownership rule.

Then integrate the paired feature revisions into the beta branches, inspect the
integration diff, and run the existing beta pipeline. Capture both image tags,
Git SHAs, migration result and authenticated acceptance separately.

## Server configuration

Set these only in the existing beta server-side configuration; never in Vite:

```dotenv
DEPLOYMENT_CHANNEL=beta
SHARED_ALLOWANCE_ENABLED=true
SHARED_ALLOWANCE_BETA_PLAN=premium
SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS=25000000
SHARED_ALLOWANCE_HISTORY_TOKENS=8000
SHARED_ALLOWANCE_REQUEST_SECONDS=900
```

Use the existing explicitly authorized `BETA_ALLOWED_USER_IDS` cohort. Confirm
the friend is in that list before granting access; do not infer an account from
usage reports. `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` must be available to the
beta API. OpenAI handles Luna, other GPT models, Flare and document/search tools.
Claude answers remain on the selected Claude model. Older conversation summaries
use Luna, including Claude chats, as explicitly approved by the owner. Flare may
reuse owned images from that same conversation: latest for an edit, up to four
recent images for a composition. Unrelated new images use no previous images.

Keep `BETA_ALLOW_PAYMENTS=false` and `BETA_ALLOW_ENTITLEMENT_MUTATIONS=false`.
Replacement-plan checkout is intentionally unavailable. The frontend obtains its
catalog and allowance from the authenticated snapshot endpoint; no frontend
feature flag or guessed grant is required. The backend refuses this feature in
the production channel even if its flag is accidentally enabled.

The beta grant is scoped separately from production, issued once per user per
UTC calendar month, and expires at the next month boundary. The provisional
Premium grant is 6,250,000 internal units (5× Start); Luna/background work has its
own 1,000,000-unit cap. The aggregate guard is 25,000,000 units per beta month.
These are standard-rate accounting units, not cash balances. The guard includes
completed supplier usage and conservative exposure for unknown attempts. Hosted
tool/provider variance can exceed an estimate; this is not a guaranteed invoice
spending cap. Tune these values after feedback, not by relabeling the UI multiple.

## Operational checks

- Snapshot: `GET /api/v1/user/usage/me/allowance` must show the beta grant, correct
  renewal and seven-model catalog for an allowed account, with no stacked old
  reply or image-energy grant.
- Send: the signed estimate and optional request ceiling precede reservation.
  Every controlled provider step checks remaining reservation and the aggregate
  guard. A duplicate `client_request_id` returns the same assistant ID.
- Settlement: known successful usage settles once before the terminal event.
  Failed/cancelled tasks are platform-funded in beta. Unknown supplier usage
  stays pending; viewing the snapshot releases abandoned customer holds after
  the 900-second request limit plus 300 seconds of grace. Unknown supplier
  exposure remains in the guard. Provider IDs are captured as streams start.
- Reconciliation: inspect `allowance_provider_attempt` counters, usage details,
  provider ID and timestamps against provider usage/invoices. Do not turn unknown
  usage into zero or rewrite old events to refill a balance. Manual audited grant
  tooling and private-tier capacity mapping are follow-up operations work.
- Stop: set `SHARED_ALLOWANCE_ENABLED=false` and restart beta to restore the old
  path without deleting accounting. Historical conversations that selected new
  models may require selecting a model from the restored catalog.

## Acceptance still required on deployed beta

Verify actual authenticated login, text/stream/resume/cancel, web sources,
document citations, uploaded-image vision, Flare generation and follow-up edit.
Run the capability matrix across every advertised model before public release;
local shared-tool checks are representative, not every model/tool permutation.
Verify public image URLs and storage retention with production-backed beta data.
Run mobile and desktop checks on the deployed assets, including exhaustion and
network failures. Confirm the private feedback account and record friend feedback.

Public checkout, production cutover, private-tier capacity amounts and verified
payment-fee economics remain outside this beta implementation.
