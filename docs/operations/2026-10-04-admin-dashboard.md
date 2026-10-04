# Owner dashboard, read-only v1

Implementation scope: backend `/api/v1/admin/dashboard/*` and frontend
`/admin/usage`, reached from Settings for an explicitly authorized signed-in owner.
The existing `/admin/panel` broadcast workflow is preserved.

## Views

- Overview: selected-period active users, logical tasks/completions/failures,
  recorded estimated provider cost, failed-work cost, coverage warnings, recorded
  payment totals per currency, current internal supplier/loss guards, model/feature
  breakdowns and current low-balance/renewal-failure counts.
- Users: server-side search, stable pagination, cost/task/charge/purchase/remaining/
  recent-activity sorting; paying/private/trial/configured-test cohorts. Click a
  user for current balances, subscription history, selected-period costs and
  purchases, paginated tasks, and shared-task provider-attempt drill-down.
- Purchases: creation-date window and current recorded status, product, amount,
  currency, renewal failure reason and **current** matching entitlement activity.
- Settings/pricing: effective runtime flags, bounded new-policy tool limits,
  supplier/loss budgets, configured shared offers/rates, stored historical-pricing
  catalog, active text-model catalog/provider pause, subscription/pack catalogs,
  image credit rates and document/audio limits. No settings editor.
- Browser users/purchases CSV exports cover the **displayed page**, including
  stable IDs and UTC source timestamps. CSV fields are quoted and spreadsheet
  formula prefixes escaped. Unsupported download actions are hidden in Telegram.

## Accounting definitions and known coverage limits

1. One `AllowanceRequest` is one logical task. `RequestLedger` contributes only
   where a shared parent with the same user/request identity does not exist.
   Provider attempts, payment rows and overlapping subscriptions are aggregated
   independently before joining users, so they cannot multiply task counts or
   customer charges. Counts use task creation time, `[start, end)` in UTC.
2. Shared customer debits sum immutable parent `charged` and `luna_charged`.
   Historical legacy reply/image/minute debits have different units; do not add
   their `RequestLedger.cost` to shared units. Current legacy text/image/energy/
   transcription balances appear separately in user detail using existing rules.
3. Shared provider cost uses recorded child `supplier_units`. Legacy token usage
   uses its stored USD `total_cost`, never today's price to reprice history. Token
   rows duplicated by a same-user/request/model child or a provider-response ID
   are excluded; distinct legacy helper models remain visible. Failed logical
   work retains successful child/legacy attempt costs.
4. These are **priced estimates**, not supplier invoices or subsidy reconciliation.
   Zero-priced token usage with nonzero counters and non-USD rows are explicitly
   incomplete. Tasks without provider-cost records are counted as missing coverage;
   this matters for audio and older failure paths. No missing cost is assumed free.
5. Unknown shared attempts contribute their budgets to **unresolved exposure**,
   not completed spend. Exposure includes all older unresolved attempts. The
   current-month internal guard reuses `allowance_tasks.exposure`, includes test
   traffic and older unresolved work, and covers shared-allowance attempts only.
   Filtering a business report must never enlarge the real internal guard.
6. Current shared balances are recorded rows in the effective accounting scope
   (plus the single lifetime trial). Read-only reports do not create a new period,
   start trials, change a grant, settle/cancel work or revive expired accounts.
   No initialized current row means "Not initialized", not a zero grant.
   Active subscription/access information is separate from recorded capacity.
7. Payments are grouped by their **creation date and current stored status**.
   Confirmed amounts and recorded full-refund amounts are separate, by currency.
   They are not period cash flow, realized net revenue/profit, or a bank statement.
   Refunds/webhook history, partial refunds, fees and invoice reconciliation remain
   separate work. Private/free grants do not create payment revenue.
8. Current matching entitlement activity does not prove fulfillment of that
   individual subscription payment: there is no reliable historical payment-to-
   subscription-period foreign key. A correctly expired/cancelled purchase can
   show inactive access; treat apparent mismatches as investigation leads.
9. Beta and production can share the database/accounting scope. Reports describe
   that shared population once, not two independent businesses. Deployment flags
   belong to the backend that answered. Snapshot read time, actual replica status
   and replica last-replay time are reported; a read timestamp is not ingestion
   freshness. Configure synthetic test UUIDs explicitly; defaults exclude none.

## Access and rollout

Use existing normal cookie/Bearer login. Set either/both comma-separated lists on
backend deployments:

- `ADMIN_DASHBOARD_USER_IDS`: owner AppUser UUIDs (preferred for browser identities).
- `ADMIN_DASHBOARD_TELEGRAM_IDS`: owner's existing linked Telegram IDs.
- `ADMIN_DASHBOARD_TEST_USER_IDS`: explicitly identified synthetic/test AppUser UUIDs.

Empty owner lists deny all. No browser `VITE_*` admin IDs, pasted shared secret,
client-side role, or existing broadcast token grants dashboard access. `/access`
returns only a boolean; every business route independently requires authenticated
allowlist membership. Deleted accounts cannot authenticate. Report responses are
`Cache-Control: private, no-store`; the frontend uses account-specific ephemeral
report queries and cancels obsolete reads. Settings navigation fails closed.

Production reads settings from `backend-env`; beta uses `backend-beta-env`. Add the
allowlist through the existing approved configuration workflow and restart/redeploy
the affected backend. These identity IDs are deployment configuration, not newly
issued credentials. Owner membership is deliberately not hardcoded by this PR.
Do not change provider activation or funding limits to enable reporting.

Reports run on configured `read_engine` in a repeatable-read, READ ONLY transaction
with a 15-second statement timeout. This remains read-only if the configured URL
points to the primary. No production queries or provider calls are needed for tests.

API: GET `/access`, `/overview`, `/users`, `/users/{uuid}`,
`/users/{uuid}/tasks`, `/tasks/{uuid}/attempts`, `/purchases`, `/settings`.
Report windows require timezone-aware timestamps and at most 366 days; pagination
has bounded page sizes; user sorting/cohorts are validated enums and all values
are SQL bound parameters. Only explicit, safe fields are returned: no message
contents, transcripts, execution plans, raw provider payloads, auth/session tokens,
bank/card snapshots, connection URLs, API keys, or allowlist membership dumps.

## Validation and release gate

Disposable local PostgreSQL, one test worker; real bearer/cookie auth, every business
route, immutable accounting, duplicate ledgers, distinct helpers, successful work
on failed parents, time boundaries, currency separation, stored refunds, test
exclusions, overlapping grants, missing cost coverage and read-only feature balances.
Browser acceptance uses synthetic users/payments and the real local admin/auth API.
EN/RU desktop/mobile layouts, Settings entry, ranking/search, user/task drill-down,
purchases and expandable pricing are exercised in Codex's in-app browser.

CSV content/formula protection is unit-tested. The in-app browser download event
could not be observed, so actual OS download delivery is not claimed.

What's New: **not required**. This is owner-only internal reporting; it changes no
customer generation, allowance, payment or settings workflow. No shared-feed rows
or announcements are inserted. Versions: backend2.1.1 ->2.2.0;
frontend2.1.1 ->2.2.0, compatible new capabilities, based on the last verified
production release and current remote bases. Recheck deployed versions at release.
There is no DB schema migration, merge, deployment or policy/provider activation
in this implementation task. Frontend and backend PRs are companions: deploy the
backend API before/simultaneously with frontend, then configure/verify owner access.

Later scope: audited allowance/config/pricing writes; provider-invoice/subsidy
reconciliation; lifecycle/partial-refund revenue events; precise historical
subscription fulfillment links; audio attempt-cost coverage; conversion/retention
cohorts; latency percentiles and alerts; fully projected uninitialized grants.
