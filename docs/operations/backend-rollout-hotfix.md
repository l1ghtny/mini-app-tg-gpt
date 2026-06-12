# Backend Hotfix Rollout

This repo now defines the backend deployment flow in checked-in scripts under `scripts/release/` so TeamCity only needs to pass parameters.

## Standard release modes

- `RELEASE_MODE=auto`
  - resolves to `hotfix` when the TeamCity source build contains exactly one VCS change
  - also resolves to `hotfix` when the latest shipped commit message includes `[hotfix]` or `#hotfix`
  - otherwise resolves to `normal`
- `RELEASE_MODE=normal`
  - runs the migration job
  - updates `deployment/tg-mini-backend`
  - leaves Argo Rollouts to pause at the configured canary gates from [k8s/argo-rollouts/tg-mini-backend-rollout.yaml](../../k8s/argo-rollouts/tg-mini-backend-rollout.yaml)
- `RELEASE_MODE=hotfix`
  - runs the same migration job
  - updates `deployment/tg-mini-backend`
  - immediately calls `kubectl argo rollouts promote --full` on `rollout/tg-mini-backend`

Use `hotfix` only for production fixes that need to move past the paused canary flow quickly. The preferred branch flow is still:

1. branch from the currently deployed production commit or release tag
2. ship the minimal fix
3. deploy with `RELEASE_MODE=hotfix`
4. merge or cherry-pick the fix back into the main development branch

## Scripts

- `scripts/release/install_k8s_tools.sh`
  - installs `kubectl` and the `kubectl-argo-rollouts` plugin on the build agent
- `scripts/release/run_migration.sh`
  - renders and runs the Kubernetes migration job from `k8s/migrate-job.yaml.tpl`
- `scripts/release/deploy_backend.sh`
  - updates the backend deployment image
  - resolves `RELEASE_MODE=auto` via `scripts/release/resolve_release_mode.sh`
  - auto-promotes only when the resolved mode is `hotfix`
- `scripts/release/resolve_release_mode.sh`
  - reads TeamCity runtime properties from `TEAMCITY_BUILD_PROPERTIES_FILE` when available
  - queries TeamCity for the source build change count and latest change comment
  - falls back to the latest local git commit message when TeamCity metadata is unavailable
- `scripts/release/promote_backend_rollout.sh`
  - manual promotion helper for normal canary releases
  - set `PROMOTE_FULL=true` to skip remaining canary pauses
- `scripts/release/abort_backend_rollout.sh`
  - aborts the active rollout

## TeamCity changes required

Current TeamCity server-side config for `MiniAppTgGpt_Migration` still uses inline shell:

- `RunMigration`
- `UpdateBackend`

To use the repo-defined rollout flow, change that build configuration to:

1. Replace the kubectl install script with:
   - `bash scripts/release/install_k8s_tools.sh`
2. Replace the migration script with:
   - `bash scripts/release/run_migration.sh`
3. Replace the backend update script with:
   - `bash scripts/release/deploy_backend.sh`

Recommended build parameters for `MiniAppTgGpt_Migration`:

- `env.K8S_NAMESPACE=gpt`
- `env.DEPLOY_ENV=beta`
- `env.IMAGE_REGISTRY=localhost:32000`
- `env.IMAGE_NAME=tg-mini-app-backend`
- `env.SECRET_NAME=backend-env`
- `env.IMAGE_TAG=%dep.MiniAppTgGpt_BuildBackend.BUILD_NUMBER%`
- `env.RELEASE_MODE=auto`
- `env.ROLLOUT_TIMEOUT=300s`

Operational use:

- default deploy: run with `env.RELEASE_MODE=auto`
- force normal deploy: run with `env.RELEASE_MODE=normal`
- force hotfix deploy: run with `env.RELEASE_MODE=hotfix`

## Notes on frontend TeamCity

The newer deploy config `TelegramMiniAppProject_TgMiniFrontendNewUI_Deploy` already has a snapshot dependency on `TelegramMiniAppProject_TgMiniFrontendNewUI_Build`.

To mirror the backend auto-hotfix logic there, the frontend deploy entrypoint should resolve its source build id from:

- `RELEASE_MODE_SOURCE_BUILD_ID_PROPERTY=dep.TelegramMiniAppProject_TgMiniFrontendNewUI_Build.teamcity.build.id`

That lets the deploy script inspect the build step's source commit set even though the deploy build itself has no direct VCS checkout.
