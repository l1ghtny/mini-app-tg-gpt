---
name: Work start recovery
description: Ready threads without a linked run remain retryable instead of becoming empty tasks.
type: feature
---

## Context

The conversation start endpoint plans synchronously and then approves the plan.
If planning succeeds but execution approval does not attach a run, the durable
thread remains `ready`. The conversation UI previously rendered that state as an
empty task with no explanation or recovery path.

## Decision

- `POST /work-threads/{id}/retry` may approve the latest proposed plan when the
  thread is `ready`; it does not repeat the planner call.
- The operation keeps the existing idempotency-key contract and delegates run
  creation/linking to `approve_plan`.
- The frontend treats a ready thread with no run/result as a recoverable failed
  turn and offers Retry.

## Constraints

- Only a latest plan in `proposed` state may use this recovery path.
- Existing linked runs remain authoritative and idempotent.
- Do not expose provider or validation error text in the user-facing failure.
