#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
ARGO_NAMESPACE="${ARGO_NAMESPACE:-argocd}"
ARGO_APPLICATION="${ARGO_APPLICATION:-tg-mini-backend-rollouts}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-localhost:32000}"
IMAGE_NAME="${IMAGE_NAME:-tg-mini-app-backend}"
IMAGE_TAG="${IMAGE_TAG:-${BUILD_NUMBER:-}}"
ROLLOUT_NAME="${ROLLOUT_NAME:-tg-mini-backend}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-900s}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${IMAGE_TAG}" ]]; then
  echo "ERROR: IMAGE_TAG is required (or BUILD_NUMBER)." >&2
  exit 1
fi

image_ref="${IMAGE_REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

if ! command -v kubectl-argo-rollouts >/dev/null 2>&1; then
  echo "ERROR: kubectl-argo-rollouts plugin is required." >&2
  echo "Run scripts/release/install_k8s_tools.sh on the build agent before deploy." >&2
  exit 1
fi

IMAGE_NAME="${IMAGE_NAME}" IMAGE_TAG="${IMAGE_TAG}" \
  "${script_dir}/verify_registry_image.sh"

kubectl patch application "${ARGO_APPLICATION}" -n "${ARGO_NAMESPACE}" --type merge \
  -p "{\"spec\":{\"source\":{\"kustomize\":{\"images\":[\"${image_ref}\"]}}}}"
kubectl annotate application "${ARGO_APPLICATION}" -n "${ARGO_NAMESPACE}" \
  argocd.argoproj.io/refresh=hard --overwrite

for attempt in $(seq 1 120); do
  actual_image="$(kubectl get deployment tg-mini-backend -n "${K8S_NAMESPACE}" \
    -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
  if [[ "${actual_image}" == "${image_ref}" ]]; then
    break
  fi
  if [[ "${attempt}" -eq 120 ]]; then
    echo "ERROR: backend Deployment did not converge to ${image_ref}; current=${actual_image}" >&2
    exit 1
  fi
  sleep 5
done

kubectl argo rollouts status "rollout/${ROLLOUT_NAME}" -n "${K8S_NAMESPACE}" --timeout "${ROLLOUT_TIMEOUT}"

kubectl rollout status deployment/conversation-search-worker -n "${K8S_NAMESPACE}" --timeout=600s
kubectl set image deployment/tg-gpt-bot-commands -n "${K8S_NAMESPACE}" "bot=${image_ref}"
kubectl set image cronjob/cleanup-derived-images -n "${K8S_NAMESPACE}" "cleanup-derived=${image_ref}"
kubectl set image cronjob/subscription-check -n "${K8S_NAMESPACE}" "subscription-check=${image_ref}"
kubectl rollout status deployment/tg-gpt-bot-commands -n "${K8S_NAMESPACE}" --timeout=600s

echo "Automatic rollout completed for ${image_ref}."
