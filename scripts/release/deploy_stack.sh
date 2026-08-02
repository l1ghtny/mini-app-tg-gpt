#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
ARGO_NAMESPACE="${ARGO_NAMESPACE:-argocd}"
BACKEND_TAG="${BACKEND_TAG:?BACKEND_TAG is required}"
FRONTEND_TAG="${FRONTEND_TAG:?FRONTEND_TAG is required}"
RUNTIME_REGISTRY="${RUNTIME_REGISTRY:-localhost:32000}"
BACKEND_IMAGE_NAME="${BACKEND_IMAGE_NAME:-tg-mini-app-backend}"
FRONTEND_IMAGE_NAME="${FRONTEND_IMAGE_NAME:-tg-mini-frontend-new}"
BACKEND_APPLICATION="${BACKEND_APPLICATION:-tg-mini-backend-rollouts}"
FRONTEND_APPLICATION="${FRONTEND_APPLICATION:-tg-mini-frontend-rollouts}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-900s}"
DEPLOYMENT_TIMEOUT="${DEPLOYMENT_TIMEOUT:-600s}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${BACKEND_TAG}:${FRONTEND_TAG}" in
  *[!a-zA-Z0-9._:-]*)
    echo "ERROR: invalid backend or frontend image tag." >&2
    exit 1
    ;;
esac

if ! command -v kubectl-argo-rollouts >/dev/null 2>&1; then
  echo "ERROR: kubectl-argo-rollouts is required." >&2
  exit 1
fi

IMAGE_NAME="${BACKEND_IMAGE_NAME}" IMAGE_TAG="${BACKEND_TAG}" \
  "${script_dir}/verify_registry_image.sh"
IMAGE_NAME="${FRONTEND_IMAGE_NAME}" IMAGE_TAG="${FRONTEND_TAG}" \
  "${script_dir}/verify_registry_image.sh"

backend_image="${RUNTIME_REGISTRY}/${BACKEND_IMAGE_NAME}:${BACKEND_TAG}"
frontend_image="${RUNTIME_REGISTRY}/${FRONTEND_IMAGE_NAME}:${FRONTEND_TAG}"

patch_application_image() {
  local application="$1"
  local image="$2"

  kubectl patch application "${application}" -n "${ARGO_NAMESPACE}" --type merge \
    -p "{\"spec\":{\"source\":{\"kustomize\":{\"images\":[\"${image}\"]}}}}"
  kubectl annotate application "${application}" -n "${ARGO_NAMESPACE}" \
    argocd.argoproj.io/refresh=hard --overwrite
}

wait_for_deployment_image() {
  local deployment="$1"
  local expected_image="$2"
  local current_image=""

  for attempt in $(seq 1 120); do
    current_image="$(kubectl get deployment "${deployment}" -n "${K8S_NAMESPACE}" \
      -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)"
    if [[ "${current_image}" == "${expected_image}" ]]; then
      return 0
    fi
    sleep 5
  done

  echo "ERROR: ${deployment} did not converge to ${expected_image}; current=${current_image}" >&2
  return 1
}

retry_aborted_rollout() {
  local rollout="$1"
  local progressing_reason=""

  progressing_reason="$(kubectl get rollout "${rollout}" -n "${K8S_NAMESPACE}" \
    -o jsonpath='{range .status.conditions[?(@.type=="Progressing")]}{.reason}{end}')"
  if [[ "${progressing_reason}" == "RolloutAborted" ]]; then
    echo "Retrying previously aborted rollout ${rollout}."
    kubectl argo rollouts retry rollout "${rollout}" -n "${K8S_NAMESPACE}"
  fi
}

patch_application_image "${BACKEND_APPLICATION}" "${backend_image}"
wait_for_deployment_image tg-mini-backend "${backend_image}"
retry_aborted_rollout tg-mini-backend
kubectl argo rollouts status tg-mini-backend -n "${K8S_NAMESPACE}" --timeout "${ROLLOUT_TIMEOUT}"

kubectl rollout status deployment/conversation-search-worker -n "${K8S_NAMESPACE}" \
  --timeout="${DEPLOYMENT_TIMEOUT}"

kubectl set image deployment/tg-gpt-bot-commands -n "${K8S_NAMESPACE}" \
  "bot=${backend_image}"
kubectl set resources deployment/tg-gpt-bot-commands -n "${K8S_NAMESPACE}" \
  --limits="cpu=250m,memory=${BOT_COMMANDS_MEMORY_LIMIT:-512Mi}"
kubectl set image cronjob/cleanup-derived-images -n "${K8S_NAMESPACE}" \
  "cleanup-derived=${backend_image}"
kubectl set image cronjob/subscription-check -n "${K8S_NAMESPACE}" \
  "subscription-check=${backend_image}"
kubectl rollout status deployment/tg-gpt-bot-commands -n "${K8S_NAMESPACE}" \
  --timeout="${DEPLOYMENT_TIMEOUT}"

patch_application_image "${FRONTEND_APPLICATION}" "${frontend_image}"
wait_for_deployment_image tg-mini-frontend "${frontend_image}"
retry_aborted_rollout tg-mini-frontend
kubectl argo rollouts status tg-mini-frontend -n "${K8S_NAMESPACE}" --timeout "${ROLLOUT_TIMEOUT}"

backend_actual="$(kubectl get deployment tg-mini-backend -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
frontend_actual="$(kubectl get deployment tg-mini-frontend -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
search_worker_actual="$(kubectl get deployment conversation-search-worker -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
bot_actual="$(kubectl get deployment tg-gpt-bot-commands -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
cleanup_actual="$(kubectl get cronjob cleanup-derived-images -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}')"
subscription_actual="$(kubectl get cronjob subscription-check -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.jobTemplate.spec.template.spec.containers[0].image}')"

for actual in "${backend_actual}" "${search_worker_actual}" "${bot_actual}" "${cleanup_actual}" "${subscription_actual}"; do
  if [[ "${actual}" != "${backend_image}" ]]; then
    echo "ERROR: backend workload image verification failed: ${actual}" >&2
    exit 1
  fi
done

if [[ "${frontend_actual}" != "${frontend_image}" ]]; then
  echo "ERROR: frontend image verification failed: ${frontend_actual}" >&2
  exit 1
fi

echo "Release complete: backend=${backend_image} frontend=${frontend_image}"
