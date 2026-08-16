---
name: Beta ingress watch scope
description: Keep the AWG ingress controller namespace watch aligned with its RBAC scope.
type: ops
---

## Context

The AWG ingress controller serves only the `gpt` namespace. A label-based
namespace selector makes ingress-nginx perform cluster-scoped list/watch calls,
which a namespace Role cannot authorize. When those calls fail, the controller
can retain terminated pod endpoints and return 504 after a rollout.

## Decision

Use `--watch-namespace=gpt` with the existing Role and RoleBinding. Do not grant
cluster-wide access to Services, Secrets, Ingresses, or EndpointSlices solely to
support a single namespace.

## Deployment guard

After internal pod readiness succeeds, verify both the public backend readiness
endpoint and frontend health endpoint through `https://beta.app.lightny.ru`.
This catches stale ingress routing that pod-local health checks cannot detect.

## Gotcha

If the controller later needs multiple namespaces, design and review the
ClusterRole first; changing back to `--watch-namespace-selector` without it will
recreate the stale-cache failure.
