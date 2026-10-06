#!/usr/bin/env bash
set -euo pipefail
K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
BACKEND_IMAGE="${BACKEND_IMAGE:?BACKEND_IMAGE is required}"
case "${BACKEND_IMAGE}" in
  *[!a-zA-Z0-9._:/@-]*) echo "Invalid backend image" >&2; exit 1 ;;
esac
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
job_name="document-retention-grace-$(date +%s)-$$"
manifest="$(mktemp)"
trap 'rm -f "${manifest}"' EXIT
sed -e "s/__JOB_NAME__/${job_name}/g" -e "s#__BACKEND_IMAGE__#${BACKEND_IMAGE}#g"   "${repo_root}/k8s/documents/retention-migrate-job.yaml.tpl" > "${manifest}"
kubectl apply -n "${K8S_NAMESPACE}" -f "${manifest}"
wait_status=1
for ((attempt = 1; attempt <= 5; attempt++)); do
  set +e
  wait_output="$(kubectl wait -n "${K8S_NAMESPACE}" --for=condition=complete "job/${job_name}" --timeout=600s 2>&1)"
  wait_status=$?
  set -e
  printf '%s\n' "${wait_output}"
  if [[ "${wait_status}" -eq 0 ]]; then
    break
  fi
  if [[ "${wait_output}" != *"(NotFound)"* ]] || [[ "${attempt}" -eq 5 ]]; then
    break
  fi
  sleep 1
done
if [[ "${wait_status}" -ne 0 ]]; then
  kubectl logs -n "${K8S_NAMESPACE}" "job/${job_name}" --tail=30 || true
  exit 1
fi
kubectl logs -n "${K8S_NAMESPACE}" "job/${job_name}" --tail=10
sed -e "s#__BACKEND_IMAGE__#${BACKEND_IMAGE}#g"   "${repo_root}/k8s/documents/cleanup-cronjob.yaml.tpl" > "${manifest}"
kubectl apply -n "${K8S_NAMESPACE}" -f "${manifest}"
