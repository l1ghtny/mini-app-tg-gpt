# Backend Hotfix Rollout

This repo now defines the backend deployment flow in checked-in scripts under `scripts/release/` so TeamCity only needs to pass parameters.

## Standard release modes

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
  - auto-promotes only when `RELEASE_MODE=hotfix`
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
- `env.RELEASE_MODE=normal`
- `env.ROLLOUT_TIMEOUT=300s`

Operational use:

- normal deploy: run with default `env.RELEASE_MODE=normal`
- hotfix deploy: run with `env.RELEASE_MODE=hotfix`

## Notes on frontend TeamCity

The frontend TeamCity deploy config `TelegramMiniAppProject_TgMiniFrontendLovable_Deploy` already updates the stable deployment directly. It does not currently use the checked-in frontend canary scripts, so no extra change is required there for "bypass canary" behavior.
