#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:?IMAGE_NAME is required}"
IMAGE_TAG="${IMAGE_TAG:?IMAGE_TAG is required}"
IMAGE_CHECK_REGISTRY="${IMAGE_CHECK_REGISTRY:-registry.container-registry.svc.cluster.local:5000}"
IMAGE_CHECK_SCHEME="${IMAGE_CHECK_SCHEME:-http}"
IMAGE_CHECK_ATTEMPTS="${IMAGE_CHECK_ATTEMPTS:-10}"
IMAGE_CHECK_DELAY_SECONDS="${IMAGE_CHECK_DELAY_SECONDS:-3}"

case "${IMAGE_NAME}:${IMAGE_TAG}" in
  *[!a-zA-Z0-9._:/-]*)
    echo "ERROR: invalid image name or tag: ${IMAGE_NAME}:${IMAGE_TAG}" >&2
    exit 1
    ;;
esac

repository_url="${IMAGE_CHECK_SCHEME}://${IMAGE_CHECK_REGISTRY}/v2/${IMAGE_NAME}"
manifest_url="${repository_url}/manifests/${IMAGE_TAG}"
accept_header="application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json"
root_manifest="$(mktemp)"
child_manifest="$(mktemp)"
trap 'rm -f "${root_manifest}" "${child_manifest}"' EXIT

verify_blobs() {
  local manifest_file="$1"
  local digest

  while IFS= read -r digest; do
    [[ -z "${digest}" ]] && continue
    curl -fsSI "${repository_url}/blobs/${digest}" >/dev/null || return 1
  done < <(
    python3 - "${manifest_file}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)

config = manifest.get("config") or {}
if config.get("digest"):
    print(config["digest"])
for layer in manifest.get("layers") or []:
    if layer.get("digest"):
        print(layer["digest"])
PY
  )
}

verify_once() {
  local root_type
  local runtime_digest

  curl -fsS -H "Accept: ${accept_header}" "${manifest_url}" -o "${root_manifest}" || return 1
  root_type="$(
    python3 - "${root_manifest}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle).get("mediaType", ""))
PY
  )"

  case "${root_type}" in
    application/vnd.docker.distribution.manifest.list.v2+json|application/vnd.oci.image.index.v1+json)
      runtime_digest="$(
        python3 - "${root_manifest}" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    index = json.load(handle)

for descriptor in index.get("manifests") or []:
    platform = descriptor.get("platform") or {}
    if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
        print(descriptor.get("digest", ""))
        break
PY
      )"
      [[ -n "${runtime_digest}" ]] || return 1
      curl -fsS -H "Accept: ${accept_header}" \
        "${repository_url}/manifests/${runtime_digest}" -o "${child_manifest}" || return 1
      verify_blobs "${child_manifest}"
      ;;
    *)
      verify_blobs "${root_manifest}"
      ;;
  esac
}

for attempt in $(seq 1 "${IMAGE_CHECK_ATTEMPTS}"); do
  if verify_once; then
    echo "Registry contains a pullable linux/amd64 image for ${IMAGE_NAME}:${IMAGE_TAG}."
    exit 0
  fi

  if [[ "${attempt}" -lt "${IMAGE_CHECK_ATTEMPTS}" ]]; then
    echo "Image ${IMAGE_NAME}:${IMAGE_TAG} is not visible yet; retrying in ${IMAGE_CHECK_DELAY_SECONDS}s." >&2
    sleep "${IMAGE_CHECK_DELAY_SECONDS}"
  fi
done

echo "ERROR: registry does not contain a complete linux/amd64 image for ${IMAGE_NAME}:${IMAGE_TAG}; refusing to create Kubernetes workloads." >&2
exit 1
