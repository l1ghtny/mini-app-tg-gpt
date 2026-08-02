---
name: Shared-history beta architecture
description: Beta uses isolated app and Redis workloads with production PostgreSQL and R2 for account history continuity.
type: ops
---

## Context

An invited beta user must keep normal account and conversation history, so a separate database is not acceptable.

## Decision

Use `beta.app.lightny.ru` with separate frontend/backend/ingress/Redis and shared production PostgreSQL/R2. Treat all beta writes as real. Enforce both edge and authenticated user allowlists, show a persistent warning, disable destructive/external actions, and run no duplicate workers or CronJobs.

## Future use

Use additive expand/contract migrations before beta code. Deploy immutable existing image tags to beta and keep production Argo promotion automatic. Beta is a preview lane, not production traffic routing.

## Gotchas

Keep cookies host-only, verify the `app.lightny.ru` WebAuthn RP configuration on the beta subdomain, and do not start a beta database-polling worker until concurrent claim behavior is proven safe.
