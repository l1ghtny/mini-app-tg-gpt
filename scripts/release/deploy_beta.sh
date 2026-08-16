#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
BACKEND_TAG="${BACKEND_TAG:?BACKEND_TAG is required}"
FRONTEND_TAG="${FRONTEND_TAG:?FRONTEND_TAG is required}"
DEPLOYMENT_TIMEOUT="${DEPLOYMENT_TIMEOUT:-600s}"
PUBLIC_BETA_URL="${PUBLIC_BETA_URL:-https://beta.app.lightny.ru}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

wait_for_public_endpoint() {
  local url="$1"
  local attempts="${2:-12}"
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if curl --fail --silent --show-error \
      --connect-timeout 5 --max-time 15 "${url}" >/dev/null; then
      return 0
    fi
    echo "Waiting for public beta endpoint (${attempt}/${attempts}): ${url}" >&2
    sleep 5
  done

  echo "ERROR: public beta endpoint did not become healthy: ${url}" >&2
  return 1
}

case "${BACKEND_TAG}:${FRONTEND_TAG}" in
  *[!a-zA-Z0-9._:-]*)
    echo "ERROR: invalid backend or frontend image tag." >&2
    exit 1
    ;;
esac

IMAGE_NAME=tg-mini-app-backend IMAGE_TAG="${BACKEND_TAG}" \
  "${script_dir}/verify_registry_image.sh"
IMAGE_NAME=tg-mini-frontend-new IMAGE_TAG="${FRONTEND_TAG}" \
  "${script_dir}/verify_registry_image.sh"

for secret in backend-beta-env tg-mini-beta-redis-auth tg-mini-beta-work-runs-r2; do
  kubectl get secret "${secret}" -n "${K8S_NAMESPACE}" >/dev/null
done

rendered_manifest="$(mktemp)"
trap 'rm -f "${rendered_manifest}"' EXIT
sed \
  -e "s/__BACKEND_TAG__/${BACKEND_TAG}/g" \
  -e "s/__FRONTEND_TAG__/${FRONTEND_TAG}/g" \
  "${repo_root}/k8s/beta/lightny-beta.yaml.tpl" >"${rendered_manifest}"

kubectl apply -f "${rendered_manifest}"
kubectl apply -f \
  "${repo_root}/k8s/argo-rollouts/lightny-work-runs-observability.yaml"
kubectl rollout status deployment/tg-mini-beta-redis -n "${K8S_NAMESPACE}" \
  --timeout="${DEPLOYMENT_TIMEOUT}"
kubectl rollout status deployment/tg-mini-beta-backend -n "${K8S_NAMESPACE}" \
  --timeout="${DEPLOYMENT_TIMEOUT}"
kubectl rollout status deployment/tg-mini-beta-work-run-worker -n "${K8S_NAMESPACE}" \
  --timeout="${DEPLOYMENT_TIMEOUT}"
kubectl rollout status deployment/tg-mini-beta-frontend -n "${K8S_NAMESPACE}" \
  --timeout="${DEPLOYMENT_TIMEOUT}"

kubectl exec -n "${K8S_NAMESPACE}" deployment/tg-mini-beta-backend -c api -- \
  python -c 'import json, urllib.request; opener = urllib.request.build_opener(urllib.request.ProxyHandler({})); print(json.load(opener.open("http://127.0.0.1:8000/health/ready", timeout=5)))'
kubectl exec -n "${K8S_NAMESPACE}" deployment/tg-mini-beta-work-run-worker -c worker -- \
  python -c 'import json, urllib.request; opener = urllib.request.build_opener(urllib.request.ProxyHandler({})); print(json.load(opener.open("http://127.0.0.1:8000/health/ready", timeout=5)))'
kubectl exec -n "${K8S_NAMESPACE}" deployment/tg-mini-beta-frontend -c web -- \
  wget -qO- http://127.0.0.1/health.json

wait_for_public_endpoint "${PUBLIC_BETA_URL}/health/ready"
wait_for_public_endpoint "${PUBLIC_BETA_URL}/health.json"

backend_image="$(kubectl get deployment tg-mini-beta-backend -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
worker_image="$(kubectl get deployment tg-mini-beta-work-run-worker -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
frontend_image="$(kubectl get deployment tg-mini-beta-frontend -n "${K8S_NAMESPACE}" -o jsonpath='{.spec.template.spec.containers[0].image}')"
echo "Beta deployment complete: backend=${backend_image} worker=${worker_image} frontend=${frontend_image}"
