#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-localhost:32000}"
IMAGE_NAME="${IMAGE_NAME:-tg-mini-app-backend}"
IMAGE_TAG="${IMAGE_TAG:-${BUILD_NUMBER:-}}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-tg-mini-backend}"
CONTAINER_NAME="${CONTAINER_NAME:-api}"
ROLLOUT_NAME="${ROLLOUT_NAME:-tg-mini-backend}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-300s}"
RELEASE_MODE="$(printf '%s' "${RELEASE_MODE:-normal}" | tr '[:upper:]' '[:lower:]')"

if [[ -z "${IMAGE_TAG}" ]]; then
  echo "ERROR: IMAGE_TAG is required (or BUILD_NUMBER)." >&2
  exit 1
fi

case "${RELEASE_MODE}" in
  normal|hotfix)
    ;;
  *)
    echo "ERROR: RELEASE_MODE must be 'normal' or 'hotfix'." >&2
    exit 1
    ;;
esac

image_ref="${IMAGE_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

kubectl -n "${K8S_NAMESPACE}" set image "deployment/${DEPLOYMENT_NAME}" \
  "${CONTAINER_NAME}=${image_ref}"

echo "Deployment image updated to ${image_ref}."

if [[ "${RELEASE_MODE}" != "hotfix" ]]; then
  echo "Release mode is normal. Argo Rollouts will pause at the configured canary steps."
  echo "Promote manually with scripts/release/promote_backend_rollout.sh when ready."
  exit 0
fi

if ! command -v kubectl-argo-rollouts >/dev/null 2>&1; then
  echo "ERROR: kubectl-argo-rollouts plugin is required for hotfix mode." >&2
  echo "Run scripts/release/install_k8s_tools.sh on the build agent before deploy." >&2
  exit 1
fi

kubectl argo rollouts promote "rollout/${ROLLOUT_NAME}" -n "${K8S_NAMESPACE}" --full
kubectl argo rollouts status "rollout/${ROLLOUT_NAME}" -n "${K8S_NAMESPACE}" --timeout "${ROLLOUT_TIMEOUT}"

echo "Hotfix rollout fully promoted for ${image_ref}."
