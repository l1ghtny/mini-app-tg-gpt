---
name: Durable Work execution timeline
description: Work activity is an ordered public execution trace, not hidden reasoning or aggregate counters.
type: feature
---

## Context

The first Work UI inferred progress from a phase plus aggregate provider-call
counts. It could not explain what happened, survive detailed reconciliation, or
host future agent-to-user questions.

## Decision

- Persist public execution events in `work_run_activity_event`, ordered by
  `sequence` and idempotent by `(work_run_id, event_key)`.
- Keep stable action names localized in the frontend. Backend detail is limited
  to safe user-visible context such as filenames and search queries.
- Never store or expose hidden chain-of-thought, raw prompts, raw Python code,
  signed URLs, secrets, or full tool output as activity detail.
- Include the changed event in private SSE payloads and reconcile the complete
  list through the normal Work run/thread response.
- Retain the old `options.execution_activity` contract until historical and
  rolling-deployment compatibility is no longer needed.

## Future changes

New tools should emit a semantic start/completion/failure event through the same
contract. The planned `ask_user` tool should persist a waiting event, render its
question in the timeline, and resume the same durable run after an answer.
