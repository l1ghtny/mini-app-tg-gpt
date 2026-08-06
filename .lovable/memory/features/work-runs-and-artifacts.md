---
name: Durable work runs and output artifacts
description: Accepted architecture and sequencing for turning Lightny documents and research into finished files.
type: feature
---

## Context

Lightny is extending the existing multi-model workspace toward completed business work: spreadsheets, reports, comparisons, and evidence-backed research. The goal is a controlled, low-attention path to a useful paid product, not a generic autonomous agent or coding environment.

## Decision

- Keep ordinary chat on the current request-ledger, Redis Stream, SSE, and provider pipeline.
- Start the first deterministic workflow with a PostgreSQL-backed durable queue
  on `WorkRun` itself: transactional claims, leases, stable workflow IDs, and
  idempotent artifact keys. Keep the executor behind the worker boundary so it
  can move to DBOS once multi-provider workflows justify a separate DBOS system
  database and operational ownership.
- Name durable executions `WorkRun`; `WorkflowKind` remains the five lightweight chat presets.
- Link work runs and artifacts to existing `ChatFolder` projects through `folder_id`.
- Keep Lightny PostgreSQL authoritative for product/billing/artifact state,
  store private source/output files in R2, and use Redis only for live progress.
  If DBOS is introduced later, its system database remains execution state only.
- Persist original uploaded documents privately in R2 before relying on deterministic rendering, provider switching, or page-level evidence.
- Start with CSV/XLSX commercial-offer comparison and a deterministic XLSX renderer.
- Pilot OpenAI Code Interpreter only after deterministic artifacts; Hosted Shell comes later. Gemini code execution is an analysis helper whose structured output is rendered by Lightny.
- Do not publish work-run quotas until 50-100 representative runs establish p90/p95 cost, failure, refund, and repeat-use behavior.

## Apply in future changes

- Use stable client request IDs, `WorkRun.id` as the durable workflow identity,
  and stable provider-operation keys.
- Treat external provider timeout-after-submission as ambiguous, not automatically retryable.
- Make artifacts immutable and create revisions with parent links.
- Reconcile from PostgreSQL after Redis/SSE loss.
- Preserve beta side-effect guardrails, additive migrations, and blue/green workflow-version draining.
- See `docs/product-strategy/14-work-runs-and-artifacts-development-plan.md` for the canonical schema, API, milestones, tests, and release gates.

## Gotchas

- Current document ingestion deletes its temporary source after provider indexing; source R2 persistence is a prerequisite.
- Current frontend message normalization flattens non-image content into text; artifact cards require an additive message `artifacts` contract.
- Current `RequestLedger` feature constraint has no `work` value and the existing reservation helper commits independently; create the work run and reservation atomically in a dedicated service.
- Container price and billing semantics can change. Store price versions and reconcile actual provider usage rather than hardcoding historical cost tables.
