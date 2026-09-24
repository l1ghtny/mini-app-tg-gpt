#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
BACKEND_IMAGE="${BACKEND_IMAGE:?BACKEND_IMAGE is required}"
AUDIO_CHANNEL="${AUDIO_CHANNEL:?AUDIO_CHANNEL is required}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

case "${AUDIO_CHANNEL}" in
  production) worker_name=audio-transcription-worker; env_secret=backend-env ;;
  beta) worker_name=tg-mini-beta-audio-transcription-worker; env_secret=backend-beta-env ;;
  *) echo "Invalid audio channel" >&2; exit 1 ;;
esac
case "${BACKEND_IMAGE}" in
  *[!a-zA-Z0-9._:/@-]*) echo "Invalid backend image" >&2; exit 1 ;;
esac

# Kubelet resolves existing secret references; CI does not need secret-read access.
manifest="$(mktemp)"
trap 'rm -f "${manifest}"' EXIT
sed \
  -e "s/__WORKER_NAME__/${worker_name}/g" \
  -e "s#__BACKEND_IMAGE__#${BACKEND_IMAGE}#g" \
  -e "s/__BACKEND_ENV_SECRET__/${env_secret}/g" \
  -e "s/__CHANNEL__/${AUDIO_CHANNEL}/g" \
  "${repo_root}/k8s/audio/transcription-worker.yaml.tpl" >"${manifest}"
kubectl apply -n "${K8S_NAMESPACE}" -f "${manifest}"
kubectl rollout status "deployment/${worker_name}" -n "${K8S_NAMESPACE}" \
  --timeout="${AUDIO_WORKER_DEPLOYMENT_TIMEOUT:-3000s}"
actual="$(kubectl get deployment "${worker_name}" -n "${K8S_NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}')"
if [[ "${actual}" != "${BACKEND_IMAGE}" ]]; then
  echo "Audio worker image did not converge" >&2
  exit 1
fi
