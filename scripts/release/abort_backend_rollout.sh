#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
ROLLOUT_NAME="${ROLLOUT_NAME:-tg-mini-backend}"

if ! command -v kubectl-argo-rollouts >/dev/null 2>&1; then
  echo "ERROR: kubectl-argo-rollouts plugin is required to abort the rollout." >&2
  exit 1
fi

kubectl argo rollouts abort "rollout/${ROLLOUT_NAME}" -n "${K8S_NAMESPACE}"

echo "Rollout ${ROLLOUT_NAME} aborted."
