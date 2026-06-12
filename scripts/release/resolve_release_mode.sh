#!/usr/bin/env bash
set -euo pipefail

normalize_release_mode() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'
}

decode_teamcity_property_value() {
  local value="${1:-}"

  value="${value//\\\\/\\}"
  value="${value//\\:/:}"
  value="${value//\\=/=}"
  value="${value//\\ / }"

  printf '%s' "${value}"
}

read_teamcity_property() {
  local properties_file="$1"
  local property_name="$2"
  local raw_value

  if [[ -z "${properties_file}" || ! -f "${properties_file}" ]]; then
    return 1
  fi

  raw_value="$(
    python3 - "${properties_file}" "${property_name}" <<'PY'
import sys

properties_path, property_name = sys.argv[1], sys.argv[2]

with open(properties_path, "r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
        line = line.rstrip("\r\n")
        if not line or line[0] in "#!":
            continue

        escaped = False
        separator_index = None
        for index, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char in ("=", ":"):
                separator_index = index
                break

        if separator_index is None:
            key = line
            value = ""
        else:
            key = line[:separator_index]
            value = line[separator_index + 1 :]

        key = key.rstrip(" \t\f")
        if key != property_name:
            continue

        print(value.lstrip(" \t\f"))
        break
PY
  )"

  if [[ -z "${raw_value}" ]]; then
    return 1
  fi

  decode_teamcity_property_value "${raw_value}"
}

teamcity_properties_file() {
  if [[ -n "${TEAMCITY_BUILD_PROPERTIES_FILE:-}" && -f "${TEAMCITY_BUILD_PROPERTIES_FILE}" ]]; then
    printf '%s' "${TEAMCITY_BUILD_PROPERTIES_FILE}"
    return 0
  fi

  if [[ -n "${TEAMCITY_PROPERTIES_FILE:-}" && -f "${TEAMCITY_PROPERTIES_FILE}" ]]; then
    printf '%s' "${TEAMCITY_PROPERTIES_FILE}"
    return 0
  fi

  return 1
}

load_teamcity_runtime_context() {
  local properties_file="$1"

  TC_SERVER_URL="$(read_teamcity_property "${properties_file}" "teamcity.serverUrl" || true)"
  TC_BUILD_ID="$(read_teamcity_property "${properties_file}" "teamcity.build.id" || true)"
  TC_AUTH_USER_ID="$(read_teamcity_property "${properties_file}" "system.teamcity.auth.userId" || true)"
  TC_AUTH_PASSWORD="$(read_teamcity_property "${properties_file}" "system.teamcity.auth.password" || true)"
}

teamcity_source_build_id() {
  local properties_file="$1"

  if [[ -n "${RELEASE_MODE_SOURCE_BUILD_ID:-}" ]]; then
    printf '%s' "${RELEASE_MODE_SOURCE_BUILD_ID}"
    return 0
  fi

  if [[ -n "${RELEASE_MODE_SOURCE_BUILD_ID_PROPERTY:-}" ]]; then
    read_teamcity_property "${properties_file}" "${RELEASE_MODE_SOURCE_BUILD_ID_PROPERTY}" || return 1
    return 0
  fi

  if [[ -n "${TC_BUILD_ID:-}" ]]; then
    printf '%s' "${TC_BUILD_ID}"
    return 0
  fi

  return 1
}

fetch_teamcity_build_summary() {
  local build_id="$1"
  local fields="changes(count,change(comment,version)),revisions(revision(version))"
  local api_url

  if [[ -z "${TC_SERVER_URL:-}" || -z "${TC_AUTH_USER_ID:-}" || -z "${TC_AUTH_PASSWORD:-}" ]]; then
    return 1
  fi

  api_url="${TC_SERVER_URL%/}/app/rest/builds/id:${build_id}?fields=${fields}"

  curl -fsSL \
    -u "${TC_AUTH_USER_ID}:${TC_AUTH_PASSWORD}" \
    -H "Accept: application/json" \
    "${api_url}"
}

extract_change_count() {
  python3 - <<'PY'
import json
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)

print(payload.get("changes", {}).get("count", ""))
PY
}

extract_first_change_comment() {
  python3 - <<'PY'
import json
import sys

try:
    payload = json.load(sys.stdin)
except json.JSONDecodeError:
    sys.exit(1)

changes = payload.get("changes", {}).get("change") or []
if changes:
    print(changes[0].get("comment", ""))
PY
}

latest_git_commit_message() {
  if ! command -v git >/dev/null 2>&1; then
    return 1
  fi

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 1
  fi

  git log -1 --format=%B
}

is_hotfix_flagged_message() {
  local message="${1:-}"
  local pattern="${HOTFIX_COMMIT_PATTERN:-\\[hotfix\\]|#hotfix([[:space:]]|$)}"

  [[ -n "${message}" ]] && printf '%s' "${message}" | grep -Eiq "${pattern}"
}

resolve_release_mode() {
  local requested_mode
  local properties_file=""
  local build_id=""
  local build_summary=""
  local change_count=""
  local first_change_comment=""
  local git_message=""

  requested_mode="$(normalize_release_mode "${RELEASE_MODE:-auto}")"

  case "${requested_mode}" in
    auto|normal|hotfix)
      ;;
    *)
      echo "ERROR: RELEASE_MODE must be 'auto', 'normal', or 'hotfix'." >&2
      return 1
      ;;
  esac

  if [[ "${requested_mode}" == "normal" || "${requested_mode}" == "hotfix" ]]; then
    RESOLVED_RELEASE_MODE="${requested_mode}"
    RESOLVED_RELEASE_MODE_REASON="explicit RELEASE_MODE=${requested_mode}"
    export RESOLVED_RELEASE_MODE RESOLVED_RELEASE_MODE_REASON
    return 0
  fi

  if properties_file="$(teamcity_properties_file)"; then
    load_teamcity_runtime_context "${properties_file}"
    build_id="$(teamcity_source_build_id "${properties_file}" || true)"

    if [[ -n "${build_id}" ]]; then
      build_summary="$(fetch_teamcity_build_summary "${build_id}" || true)"
      if [[ -n "${build_summary}" ]]; then
        change_count="$(printf '%s' "${build_summary}" | extract_change_count || true)"
        first_change_comment="$(printf '%s' "${build_summary}" | extract_first_change_comment || true)"

        if [[ "${change_count}" == "1" ]]; then
          RESOLVED_RELEASE_MODE="hotfix"
          RESOLVED_RELEASE_MODE_REASON="TeamCity source build ${build_id} contains exactly one VCS change"
          export RESOLVED_RELEASE_MODE RESOLVED_RELEASE_MODE_REASON
          return 0
        fi

        if is_hotfix_flagged_message "${first_change_comment}"; then
          RESOLVED_RELEASE_MODE="hotfix"
          RESOLVED_RELEASE_MODE_REASON="latest TeamCity source build change is flagged as hotfix"
          export RESOLVED_RELEASE_MODE RESOLVED_RELEASE_MODE_REASON
          return 0
        fi
      fi
    fi
  fi

  git_message="$(latest_git_commit_message || true)"
  if is_hotfix_flagged_message "${git_message}"; then
    RESOLVED_RELEASE_MODE="hotfix"
    RESOLVED_RELEASE_MODE_REASON="latest git commit message is flagged as hotfix"
    export RESOLVED_RELEASE_MODE RESOLVED_RELEASE_MODE_REASON
    return 0
  fi

  RESOLVED_RELEASE_MODE="normal"
  RESOLVED_RELEASE_MODE_REASON="no single-change or hotfix flag signal was found"
  export RESOLVED_RELEASE_MODE RESOLVED_RELEASE_MODE_REASON
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  resolve_release_mode
  printf '%s\n' "${RESOLVED_RELEASE_MODE}"
fi
