#!/usr/bin/env bash
set -euo pipefail

kubelet_args_path="${1:-/var/snap/microk8s/current/args/kubelet}"
desired_eviction_hard='--eviction-hard="memory.available<100Mi,nodefs.available<10%,imagefs.available<15%,nodefs.inodesFree<5%,imagefs.inodesFree<5%"'

if [[ ! -f "${kubelet_args_path}" ]]; then
  echo "Kubelet args file not found: ${kubelet_args_path}" >&2
  exit 1
fi

current_eviction_hard="$(grep '^--eviction-hard=' "${kubelet_args_path}" || true)"
if [[ "${current_eviction_hard}" == "${desired_eviction_hard}" ]]; then
  echo "Kubelet eviction thresholds are already configured."
  exit 0
fi

backup_path="${kubelet_args_path}.pre-eviction-hard-$(date -u +%Y%m%dT%H%M%SZ)"
cp -a "${kubelet_args_path}" "${backup_path}"

if [[ -n "${current_eviction_hard}" ]]; then
  sed -i "s|^--eviction-hard=.*|${desired_eviction_hard}|" "${kubelet_args_path}"
else
  printf '%s\n' "${desired_eviction_hard}" >>"${kubelet_args_path}"
fi

grep '^--eviction-hard=' "${kubelet_args_path}"
systemctl restart snap.microk8s.daemon-kubelite.service
systemctl is-active --quiet snap.microk8s.daemon-kubelite.service

echo "Updated kubelet eviction thresholds; backup: ${backup_path}"
