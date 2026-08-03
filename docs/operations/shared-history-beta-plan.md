# Shared-history beta environment plan

## Decision

Use `beta.app.lightny.ru` as an allowlisted production-data preview environment. It runs separate frontend, backend, ingress, Services, and Redis, while sharing production PostgreSQL and object storage so an invited user sees the same account, conversations, messages, uploads, and entitlements as in the normal app.

This is not a production canary and is not part of Argo's traffic percentages. Production canary promotion remains fully automatic; beta is a durable place to exercise a candidate build with real history before or independently of a production release.

## Isolation boundary

| Component | Beta choice | Reason |
| --- | --- | --- |
| Frontend/backend pods | Separate beta workloads | Candidate code cannot replace production pods. |
| PostgreSQL | Shared production database | Preserves normal history and account continuity. |
| R2/object storage | Shared production buckets | Existing uploads and generated assets remain visible. |
| Redis | Dedicated beta instance/database | Prevents SSE/event-bus and limiter keys from colliding with production. |
| Scheduled workers | Production only | Prevents duplicate subscription, cleanup, notification, and polling work. |
| Bot/webhook consumers | Production only | Prevents duplicate Telegram side effects. |
| Sentry | Separate `beta-prod-data` environment | Makes candidate failures filterable without losing production-data context. |

Writes in beta are real because PostgreSQL and R2 are shared. The UI must show a persistent “Beta — changes affect your real account” banner.

## Safety rules

- Enforce the allowlist before creating a beta browser session and again on every authenticated backend dependency. A hidden URL alone is not access control.
- Disable payments, account deletion, broadcasts/admin mutations, webhook registration, and other irreversible/external actions through explicit beta environment flags.
- Do not run beta-only database migrations. Use expand/contract migrations: deploy additive schema changes first, run beta and production code against the compatible schema, and remove old schema only after both have moved forward.
- Keep cookies host-only and require a fresh beta login. Do not widen production cookies to `.app.lightny.ru`.
- Configure WebAuthn for the existing `app.lightny.ru` RP ID and explicitly verify registration and authentication on the beta subdomain before launch.
- Use production secrets only where required for reading the shared data; create distinct Redis, session/cookie, ingress, and observability configuration.
- Do not start a beta copy of `conversation-search-worker` unless its database claim protocol is proven safe for concurrent versions. The initial beta release uses production workers only.

## Delivery model

Beta is an optional preview lane, not a blocking manual gate for every release:

- Production: `LightnyReleaseFlow` builds both repositories and performs the automatic Argo rollout.
- Beta: the manual `LightnyBetaFlow` builds immutable `beta-*` backend/frontend tags from the selected repository revisions and updates only the beta workloads. It never runs migrations or background workers.
- Promoting a beta-tested version means starting the production flow with those immutable tags; it does not copy data or rebuild images.

## Implementation phases

### Phase 1 — guardrails

1. Add backend settings for `DEPLOYMENT_CHANNEL=beta`, an authenticated user-ID allowlist, and kill switches for payment, deletion, admin/broadcast, and external webhook actions.
2. Add integration tests proving anonymous/unlisted users are denied and dangerous endpoints are disabled.
3. Add the frontend beta banner and point its API URL at `https://beta.app.lightny.ru`.

### Phase 2 — infrastructure

1. Provision dedicated beta Redis and secrets.
2. Add beta backend/frontend Deployments, Services, ingress, NetworkPolicies, and modest resource limits in the `gpt` namespace.
3. Issue TLS and DNS for `beta.app.lightny.ru`; expose only the login surface until an allowlisted account authenticates.
4. Configure the beta backend with production PostgreSQL/R2 and beta Redis/Sentry/cookie settings.
5. Keep all CronJobs, bot consumers, and database-polling workers absent from the beta overlay.

### Phase 3 — pipeline

1. Create `LightnyBetaFlow` with parallel beta backend/frontend builds and a `deploy_beta` job.
2. Verify both images exist before changing beta resources.
3. Run beta health, login, conversation-list, send/stream/resume, upload-read, and logout smoke tests.
4. Record the deployed backend/frontend tags visibly in TeamCity and Sentry.

### Phase 4 — launch proof

1. Sign in as each allowlisted user and confirm existing production history appears.
2. Send a beta message and confirm it appears in production, proving intentional shared writes.
3. Verify Redis keys and SSE traffic stay in beta Redis.
4. Verify no duplicate bot, payment, cleanup, subscription, or search-worker action occurs.
5. Verify passkey login on the beta hostname and rollback beta by restoring its prior image tags.

## Exit criteria

- Only allowlisted authenticated accounts can reach beta APIs.
- Existing history and files are visible, and shared-write behavior is clearly disclosed.
- Candidate failures cannot interrupt production pods, Redis streams, workers, or ingress.
- No destructive or external side-effect endpoint is reachable from beta.
- Beta deploy and rollback use immutable existing images and do not require a database copy.

## Initial implementation

- Backend access is restricted by internal account UUID after every supported authentication path and on every authenticated dependency.
- Payments, account deletion, subscription/access-code changes, broadcasts, identity changes, email delivery, and Telegram image sharing fail closed in the beta deployment channel.
- Both invited accounts can authenticate with Telegram OIDC or their existing passkeys. Email login remains disabled on beta.
- `k8s/beta/lightny-beta.yaml.tpl` defines one frontend, one backend, a persistent dedicated Redis, same-host ingress, and ingress NetworkPolicies. It defines no worker or CronJob.
- `scripts/release/create_beta_secrets.sh` derives a reduced secret from production, copying only the Telegram OIDC credentials needed for login while omitting payment, broadcast, and SMTP credentials.
- `scripts/release/deploy_beta.sh` verifies immutable registry tags before applying the rendered workload manifest and running in-pod health checks.
