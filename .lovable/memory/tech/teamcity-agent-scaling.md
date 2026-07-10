---
name: TeamCity agent scaling on microk8s
description: Additional TeamCity agents should use unique identity and per-agent PVCs instead of scaling the legacy hostPath Deployment.
type: ops
---

## Context / problem

The legacy `gamedev/teamcity-agent-deployment` is pinned to `main-server` and uses node-local `hostPath` directories for TeamCity agent config, work, temp, tools, plugins, and system state. It also has a fixed `AGENT_NAME=gamedev-teamcity-agent`.

Scaling that Deployment directly would duplicate the TeamCity agent identity and share the same local directories on `main-server`.

## Decision taken

Add extra agents as separate single-replica Deployments, pinned to specific worker nodes, with one dedicated PVC per agent. Keep a unique `AGENT_NAME` per Deployment.

Current added agent names:

- `gamedev-teamcity-agent-k8s-node-2`
- `gamedev-teamcity-agent-k8s-node-3`

## How to apply it in future changes

For each additional static agent:

- create a dedicated `ReadWriteOnce` PVC in `gamedev`
- pin the pod to the target node with `kubernetes.io/hostname`
- mount the PVC into `/data/teamcity_agent/conf` and `/opt/buildagent/{work,temp,tools,plugins,system,logs}` using subpaths
- use a PVC-backed `docker:29-dind` sidecar when the target node does not expose `/var/run/docker.sock`
- set `DOCKER_HOST=tcp://localhost:2375` on the agent container for the sidecar path
- use a stable unique `AGENT_NAME`

## Constraints / gotchas

- New agents need one-time authorization in the TeamCity UI unless an admin REST authorization path is available.
- With `microk8s-hostpath`, PVCs bind to the scheduled node because the storage class uses `WaitForFirstConsumer`.
- The target node must expose `/var/run/docker.sock` for the host socket pattern; worker nodes `k8s-node-2` and `k8s-node-3` did not, so this cluster uses privileged Docker-in-Docker sidecars for those agents.
