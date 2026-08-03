#!/usr/bin/env bash
set -euo pipefail

K8S_NAMESPACE="${K8S_NAMESPACE:-gpt}"
SOURCE_SECRET="${SOURCE_SECRET:-backend-env}"
BETA_SECRET="${BETA_SECRET:-backend-beta-env}"
BETA_ALLOWED_USER_IDS="${BETA_ALLOWED_USER_IDS:?BETA_ALLOWED_USER_IDS is required}"
BETA_BASIC_AUTH_USER="${BETA_BASIC_AUTH_USER:-lightny-beta}"
BETA_CREDENTIAL_OUTPUT_FILE="${BETA_CREDENTIAL_OUTPUT_FILE:-}"

case "${BETA_ALLOWED_USER_IDS}" in
  *[!a-fA-F0-9,-]*)
    echo "ERROR: BETA_ALLOWED_USER_IDS must be a comma-separated UUID list." >&2
    exit 1
    ;;
esac

tmp_dir="$(mktemp -d)"
chmod 700 "${tmp_dir}"
trap 'rm -f "${tmp_dir}"/*; rmdir "${tmp_dir}"' EXIT

decode_secret_key() {
  local secret="$1"
  local key="$2"
  kubectl get secret "${secret}" -n "${K8S_NAMESPACE}" -o "jsonpath={.data.${key}}" 2>/dev/null \
    | python3 -c 'import base64, sys; raw=sys.stdin.buffer.read(); sys.stdout.write(base64.b64decode(raw).decode() if raw else "")'
}

redis_password="$(decode_secret_key tg-mini-beta-redis-auth REDIS_PASSWORD || true)"
if [[ -z "${redis_password}" ]]; then
  redis_password="$(openssl rand -hex 32)"
fi

beta_secret_key="$(decode_secret_key "${BETA_SECRET}" SECRET_KEY || true)"
if [[ -z "${beta_secret_key}" ]]; then
  beta_secret_key="$(openssl rand -hex 64)"
fi

basic_password="${BETA_BASIC_AUTH_PASSWORD:-}"
if kubectl get secret tg-mini-beta-basic-auth -n "${K8S_NAMESPACE}" >/dev/null 2>&1; then
  basic_auth_line="$(decode_secret_key tg-mini-beta-basic-auth auth)"
else
  if [[ -z "${basic_password}" ]]; then
    basic_password="$(openssl rand -hex 16)"
  fi
  basic_auth_line="$(htpasswd -nbB "${BETA_BASIC_AUTH_USER}" "${basic_password}")"
fi

printf 'REDIS_PASSWORD=%s\n' "${redis_password}" >"${tmp_dir}/redis.env"
kubectl create secret generic tg-mini-beta-redis-auth \
  -n "${K8S_NAMESPACE}" \
  --from-env-file="${tmp_dir}/redis.env" \
  --dry-run=client -o yaml | kubectl apply -f -

printf '%s\n' "${basic_auth_line}" >"${tmp_dir}/auth"
kubectl create secret generic tg-mini-beta-basic-auth \
  -n "${K8S_NAMESPACE}" \
  --from-file="auth=${tmp_dir}/auth" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl get secret "${SOURCE_SECRET}" -n "${K8S_NAMESPACE}" -o json >"${tmp_dir}/source.json"
jq \
  --arg namespace "${K8S_NAMESPACE}" \
  --arg secret_name "${BETA_SECRET}" \
  --arg deployment_channel beta \
  --arg allowed_user_ids "${BETA_ALLOWED_USER_IDS}" \
  --arg redis_url "redis://:${redis_password}@tg-mini-beta-redis:6379/0" \
  --arg secret_key "${beta_secret_key}" \
  --arg webapp_url "https://beta.app.lightny.ru" \
  '
    .data as $source |
    {
      apiVersion: "v1",
      kind: "Secret",
      metadata: {name: $secret_name, namespace: $namespace},
      type: "Opaque",
      data: {
        DATABASE_URL: $source.DATABASE_URL,
        GEMINI_API_KEY: $source.GEMINI_API_KEY,
        IMAGE_FETCH_PROXY_ALLOWED_HOSTS: $source.IMAGE_FETCH_PROXY_ALLOWED_HOSTS,
        OPENAI_API_KEY: $source.OPENAI_API_KEY,
        OPENAI_CHAINING_ENABLED: $source.OPENAI_CHAINING_ENABLED,
        OPENAI_CHAIN_MAX_INACTIVITY_DAYS: $source.OPENAI_CHAIN_MAX_INACTIVITY_DAYS,
        PASSKEY_RP_NAME: $source.PASSKEY_RP_NAME,
        PERPLEXITY_API_KEY: $source.PERPLEXITY_API_KEY,
        R2_ACCESS_KEY_ID: $source.R2_ACCESS_KEY_ID,
        R2_BUCKET: $source.R2_BUCKET,
        R2_ENDPOINT: $source.R2_ENDPOINT,
        R2_OPENAI_PUBLIC_BASE_URL: $source.R2_OPENAI_PUBLIC_BASE_URL,
        R2_PUBLIC_BASE_URL: $source.R2_PUBLIC_BASE_URL,
        R2_REGION: $source.R2_REGION,
        R2_SECRET_ACCESS_KEY: $source.R2_SECRET_ACCESS_KEY,
        SENTRY_DSN: $source.SENTRY_DSN,
        STARTER_BUNDLE: $source.STARTER_BUNDLE,
        DEPLOYMENT_CHANNEL: ($deployment_channel | @base64),
        BETA_ALLOWED_USER_IDS: ($allowed_user_ids | @base64),
        REDIS_URL: ($redis_url | @base64),
        SECRET_KEY: ($secret_key | @base64),
        ENVIRONMENT: ("beta_prod_data" | @base64),
        WEBAPP_URL: ($webapp_url | @base64),
        WEB_AUTH_ENABLED: ("true" | @base64),
        DEBUG_MODE: ("false" | @base64),
        TELEGRAM_OIDC_ENABLED: ("false" | @base64),
        CORS_ALLOWED_ORIGINS: ($webapp_url | @base64),
        PASSKEY_RP_ID: ("app.lightny.ru" | @base64),
        PASSKEY_ALLOWED_ORIGINS: ($webapp_url | @base64),
        AUTH_COOKIE_NAME: ("lightny_beta_session" | @base64),
        AUTH_COOKIE_SECURE: ("true" | @base64),
        AUTH_COOKIE_SAMESITE: ("lax" | @base64),
        WEB_AUTH_TRUSTED_PROXY_CIDRS: ("10.1.0.0/16,10.77.0.2/32" | @base64)
      }
    }
  ' "${tmp_dir}/source.json" >"${tmp_dir}/beta-secret.json"
kubectl apply -f "${tmp_dir}/beta-secret.json"

if [[ -n "${BETA_CREDENTIAL_OUTPUT_FILE}" && -n "${basic_password}" ]]; then
  umask 077
  {
    printf 'username=%s\n' "${BETA_BASIC_AUTH_USER}"
    printf 'password=%s\n' "${basic_password}"
  } >"${BETA_CREDENTIAL_OUTPUT_FILE}"
fi

echo "Beta secrets are configured without payment, broadcast, SMTP, or Telegram OIDC credentials."
