#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
ROLLOUT_NAME="${ROLLOUT_NAME:-tg-mini-backend}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"
PROMOTE_FULL="$(printf '%s' "${PROMOTE_FULL:-false}" | tr '[:upper:]' '[:lower:]')"

if ! command -v kubectl-argo-rollouts >/dev/null 2>&1; then
  echo "ERROR: kubectl-argo-rollouts plugin is required to promote the rollout." >&2
  exit 1
fi

args=("argo" "rollouts" "promote" "rollout/${ROLLOUT_NAME}" "-n" "${K8S_NAMESPACE}")
if [[ "${PROMOTE_FULL}" == "true" ]]; then
  args+=("--full")
fi

kubectl "${args[@]}"
kubectl argo rollouts status "rollout/${ROLLOUT_NAME}" -n "${K8S_NAMESPACE}" --timeout "${ROLLOUT_TIMEOUT}"

echo "Rollout ${ROLLOUT_NAME} promoted."
