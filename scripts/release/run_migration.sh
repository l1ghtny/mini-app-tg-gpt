#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
DEPLOY_ENV="${DEPLOY_ENV:-beta}"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-localhost:32000}"
IMAGE_NAME="${IMAGE_NAME:-tg-mini-app-backend}"
IMAGE_TAG="${IMAGE_TAG:-${BUILD_NUMBER:-}}"
SECRET_NAME="${SECRET_NAME:-backend-env}"
JOB_SUFFIX="${JOB_SUFFIX:-${BUILD_NUMBER:-}}"
JOB_TIMEOUT="${JOB_TIMEOUT:-300s}"
JOB_TEMPLATE="${JOB_TEMPLATE:-k8s/migrate-job.yaml.tpl}"

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

sed \
  -e "s/__K8S_NAMESPACE__/${K8S_NAMESPACE}/g" \
  -e "s/__DEPLOY_ENV__/${DEPLOY_ENV}/g" \
  -e "s#__IMAGE_REGISTRY__#${IMAGE_REGISTRY}#g" \
  -e "s/__IMAGE_NAME__/${IMAGE_NAME}/g" \
  -e "s/__IMAGE_TAG__/${IMAGE_TAG}/g" \
  -e "s/__SECRET_NAME__/${SECRET_NAME}/g" \
  -e "s/__JOB_SUFFIX__/${JOB_SUFFIX}/g" \
  "${JOB_TEMPLATE}" > "${tmpfile}"

kubectl apply -f "${tmpfile}"

set +e
kubectl wait -n "${K8S_NAMESPACE}" \
  --for=condition=complete "job/${JOB_NAME}" \
  --timeout="${JOB_TIMEOUT}"
wait_status=$?
set -e

if [[ "${wait_status}" -ne 0 ]]; then
  echo "Migration job failed or did not complete in time."
  echo "===== Job describe ====="
  kubectl describe -n "${K8S_NAMESPACE}" "job/${JOB_NAME}" || true
  echo "===== Job logs ====="
  pod_name="$(kubectl get pods -n "${K8S_NAMESPACE}" -l "job-name=${JOB_NAME}" -o jsonpath='{.items[0].metadata.name}')"
  kubectl logs -n "${K8S_NAMESPACE}" "${pod_name}" || true
  exit 1
fi

echo "Migration completed successfully."
