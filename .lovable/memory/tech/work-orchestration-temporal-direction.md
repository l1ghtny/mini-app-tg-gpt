---
name: Temporal is the Work orchestration target
description: Use Temporal rather than DBOS if Work later needs a dedicated durable-workflow layer.
type: tech
---

## Context

An earlier Work proposal selected DBOS for durable orchestration. Productionizing and
operating DBOS is not the desired path for Lightny. The current application already
has a working conversation-native Work execution path, so changing orchestration is
not part of the first MVP gate.

## Decision

Temporal is the sole long-term target for a dedicated Work orchestration layer. Do not
introduce DBOS dependencies, schemas, workers, deployment assumptions, or contracts.
Treat DBOS references in historical branches or recovery checkpoints as superseded.

## How to apply this

- Finish and validate the existing Work runtime before starting a migration.
- Introduce Temporal only when measured scale, recovery, workflow-versioning, or
  operational evidence justifies the added infrastructure.
- Preserve PostgreSQL as the product source of truth for run, entitlement, artifact,
  evidence, and accounting records.
- Keep API, SSE, artifact, clarification, cancellation, and billing contracts stable
  across an incremental migration.
- Model provider calls and other costly side effects as idempotent or explicitly
  reconciliation-aware activities.

## Constraints and gotchas

- Temporal does not remove the need for application-level idempotency or provider
  reconciliation after ambiguous external-call outcomes.
- Hosting, backups, upgrades, observability, worker-version compatibility, and
  incident ownership must be production-ready before cutover.
- Do not block the first Work pilot on Temporal adoption.
