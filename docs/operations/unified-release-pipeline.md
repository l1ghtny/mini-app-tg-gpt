# Unified Lightny release pipeline

## Target graph

The TeamCity pipeline is versioned in `ops/teamcity/lightny-release-pipeline.yaml` and replaces the independent backend build, migration, frontend build/deploy, and manual promotion configurations.

1. `build_backend` and `build_frontend` run in parallel and push immutable build-number tags plus `latest`. Provenance attestations are disabled because the current registry has retained OCI indexes whose referenced runtime manifests were missing.
2. `run_migrations` waits for the backend image, verifies its registry manifest exists, and only then creates the Alembic Job.
3. `deploy` waits for both images and the migration. It patches the Argo CD image overrides, waits for the automatic backend and frontend Rollouts, then updates every backend-image consumer:
   - `tg-mini-backend`
   - `conversation-search-worker`
   - `tg-gpt-bot-commands`
   - `cleanup-derived-images`
   - `subscription-check`
4. The job fails unless all workload image references match the expected immutable tags.

The frontend build intentionally no longer sets `VITE_CANARY_TG_IDS`; named-user preview traffic belongs on the beta hostname, not in the production canary.

## Canary policy

Both production Rollouts now use the same automatic sequence:

1. Start one candidate pod at 0% traffic.
2. Run five direct readiness probes against the canary Service.
3. Send 20% traffic for two minutes and repeat the readiness analysis.
4. Send 100% traffic for two minutes and repeat the readiness analysis.
5. Promote automatically when all analysis passes.

An analysis failure aborts the Rollout and keeps the previous stable ReplicaSet serving traffic. A progress deadline also aborts a rollout that cannot become ready. There are no indefinite pause steps and no normal-path `promote --full` command.

This health analysis is deterministic but not a substitute for error-rate analysis. Add a Sentry or Prometheus metric when a reliable per-version production signal is available.

## TeamCity cutover

1. Enable TeamCity Pipelines access for `Telegram-mini-app-project`. The server currently shows the Pipelines EAP access screen for this project even though `CyberColors` already has access.
2. Move the existing backend and frontend VCS roots to the common `Telegram-mini-app-project` parent. Their IDs and credentials remain intact, and the old child configurations can continue inheriting them.
3. Create `LightnyReleaseFlow` under that project by importing the versioned YAML.
4. Add the existing frontend `env.SENTRY_AUTH_TOKEN` as a password parameter on the new pipeline. The build works without it, but source-map upload is skipped.
5. Run one manual pipeline build and prove registry tags, migration completion, both Rollouts, and all four background resources.
6. Add VCS triggers for backend `master` and frontend `main` only after the first green run.
7. Disable the triggers on the old backend, migration, and frontend configurations. Keep them for rollback until two unified releases have succeeded; do not delete them during cutover.

## Failure behavior

- A missing or incomplete image fails before Kubernetes creates a migration or rollout workload. The preflight follows an OCI index to its Linux/amd64 manifest and verifies the referenced config and layer blobs.
- A migration failure prevents all deployment jobs.
- A backend rollout failure prevents unmanaged workers and the frontend from changing.
- A frontend rollout failure leaves the already-verified backend release active and fails the pipeline visibly.
- Argo CD self-heal preserves the selected versions because the pipeline changes each Application's Kustomize image override instead of mutating an Argo-owned Deployment directly.
