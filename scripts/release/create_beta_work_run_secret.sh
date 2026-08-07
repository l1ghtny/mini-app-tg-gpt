#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
SECRET_NAME="${SECRET_NAME:-tg-mini-beta-work-runs-r2}"
PRIVATE_DOCUMENTS_ENV_FILE="${PRIVATE_DOCUMENTS_ENV_FILE:?PRIVATE_DOCUMENTS_ENV_FILE is required}"

if [[ ! -r "${PRIVATE_DOCUMENTS_ENV_FILE}" ]]; then
  echo "ERROR: private documents environment file is not readable." >&2
  exit 1
fi

for key in \
  R2_PRIVATE_DOCUMENTS_BUCKET \
  R2_PRIVATE_DOCUMENTS_ACCESS_KEY_ID \
  R2_PRIVATE_DOCUMENTS_SECRET_ACCESS_KEY; do
  if ! grep -q "^${key}=..*" "${PRIVATE_DOCUMENTS_ENV_FILE}"; then
    echo "ERROR: ${key} is required in the private documents environment file." >&2
    exit 1
  fi
done

kubectl create secret generic "${SECRET_NAME}" \
  -n "${K8S_NAMESPACE}" \
  --from-env-file="${PRIVATE_DOCUMENTS_ENV_FILE}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Beta private document storage secret is configured."
