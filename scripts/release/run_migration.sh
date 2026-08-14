#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
DEPLOY_ENV="${DEPLOY_ENV:-production}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-localhost:32000}"
IMAGE_NAME="${IMAGE_NAME:-tg-mini-app-backend}"
IMAGE_TAG="${IMAGE_TAG:-${BUILD_NUMBER:-}}"
SECRET_NAME="${SECRET_NAME:-backend-env}"
JOB_SUFFIX="${JOB_SUFFIX:-${BUILD_NUMBER:-}}"
JOB_TIMEOUT="${JOB_TIMEOUT:-300s}"
JOB_VISIBILITY_ATTEMPTS="${JOB_VISIBILITY_ATTEMPTS:-30}"
JOB_VISIBILITY_RETRY_DELAY_SECONDS="${JOB_VISIBILITY_RETRY_DELAY_SECONDS:-1}"
JOB_WAIT_NOT_FOUND_RETRIES="${JOB_WAIT_NOT_FOUND_RETRIES:-5}"
JOB_TEMPLATE="${JOB_TEMPLATE:-k8s/migrate-job.yaml.tpl}"
MIGRATION_MODE="${MIGRATION_MODE:-upgrade}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY_REGISTRY_SCRIPT="${VERIFY_REGISTRY_SCRIPT:-${script_dir}/verify_registry_image.sh}"

case "${MIGRATION_MODE}" in
  upgrade)
    ALEMBIC_COMMAND='["alembic", "upgrade", "head"]'
    JOB_ACTION="Migration"
    ;;
  check)
    ALEMBIC_COMMAND='["alembic", "current", "--check-heads"]'
    JOB_ACTION="Shared schema verification"
    ;;
  *)
    echo "ERROR: MIGRATION_MODE must be 'upgrade' or 'check'." >&2
    exit 1
    ;;
esac

if [[ -z "${IMAGE_TAG}" ]]; then
  echo "ERROR: IMAGE_TAG is required (or BUILD_NUMBER)." >&2
  exit 1
fi

if [[ -z "${JOB_SUFFIX}" ]]; then
  echo "ERROR: JOB_SUFFIX is required (or BUILD_NUMBER)." >&2
  exit 1
fi

if [[ ! -f "${JOB_TEMPLATE}" ]]; then
  echo "ERROR: template not found: ${JOB_TEMPLATE}" >&2
  exit 1
fi

JOB_NAME="tg-mini-backend-${JOB_SUFFIX}"
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT

IMAGE_NAME="${IMAGE_NAME}" IMAGE_TAG="${IMAGE_TAG}" \
  "${VERIFY_REGISTRY_SCRIPT}"

kubectl delete -n "${K8S_NAMESPACE}" "job/${JOB_NAME}" --ignore-not-found=true

sed \
  -e "s/__K8S_NAMESPACE__/${K8S_NAMESPACE}/g" \
  -e "s/__DEPLOY_ENV__/${DEPLOY_ENV}/g" \
  -e "s#__IMAGE_REGISTRY__#${IMAGE_REGISTRY}#g" \
  -e "s/__IMAGE_NAME__/${IMAGE_NAME}/g" \
  -e "s/__IMAGE_TAG__/${IMAGE_TAG}/g" \
  -e "s/__SECRET_NAME__/${SECRET_NAME}/g" \
  -e "s/__JOB_SUFFIX__/${JOB_SUFFIX}/g" \
  -e "s#__ALEMBIC_COMMAND__#${ALEMBIC_COMMAND}#g" \
  "${JOB_TEMPLATE}" > "${tmpfile}"

kubectl apply -f "${tmpfile}"

job_visible=false
for ((attempt = 1; attempt <= JOB_VISIBILITY_ATTEMPTS; attempt++)); do
  if kubectl get -n "${K8S_NAMESPACE}" "job/${JOB_NAME}" >/dev/null 2>&1; then
    job_visible=true
    break
  fi
  sleep "${JOB_VISIBILITY_RETRY_DELAY_SECONDS}"
done

if [[ "${job_visible}" != "true" ]]; then
  echo "${JOB_ACTION} job was created but did not become visible."
  exit 1
fi

wait_status=1
for ((attempt = 1; attempt <= JOB_WAIT_NOT_FOUND_RETRIES; attempt++)); do
  set +e
  wait_output="$(
    kubectl wait -n "${K8S_NAMESPACE}" \
      --for=condition=complete "job/${JOB_NAME}" \
      --timeout="${JOB_TIMEOUT}" 2>&1
  )"
  wait_status=$?
  set -e
  printf '%s\n' "${wait_output}"

  if [[ "${wait_status}" -eq 0 ]]; then
    break
  fi
  if [[ "${wait_output}" != *"(NotFound)"* ]] || [[ "${attempt}" -eq "${JOB_WAIT_NOT_FOUND_RETRIES}" ]]; then
    break
  fi
  sleep "${JOB_VISIBILITY_RETRY_DELAY_SECONDS}"
done

if [[ "${wait_status}" -ne 0 ]]; then
  echo "${JOB_ACTION} job failed or did not complete in time."
  echo "===== Job describe ====="
  kubectl describe -n "${K8S_NAMESPACE}" "job/${JOB_NAME}" || true
  echo "===== Job logs ====="
  pod_name="$(
    kubectl get pods -n "${K8S_NAMESPACE}" -l "job-name=${JOB_NAME}" \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
  )"
  if [[ -n "${pod_name}" ]]; then
    kubectl logs -n "${K8S_NAMESPACE}" "${pod_name}" || true
  else
    echo "No migration pod was created."
  fi
  exit 1
fi

echo "${JOB_ACTION} completed successfully."
