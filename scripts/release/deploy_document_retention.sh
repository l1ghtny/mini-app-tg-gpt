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
if ! kubectl wait -n "${K8S_NAMESPACE}" --for=condition=complete "job/${job_name}" --timeout=600s; then
  kubectl logs -n "${K8S_NAMESPACE}" "job/${job_name}" --tail=30
  exit 1
fi
kubectl logs -n "${K8S_NAMESPACE}" "job/${job_name}" --tail=10
sed -e "s#__BACKEND_IMAGE__#${BACKEND_IMAGE}#g"   "${repo_root}/k8s/documents/cleanup-cronjob.yaml.tpl" > "${manifest}"
kubectl apply -n "${K8S_NAMESPACE}" -f "${manifest}"
