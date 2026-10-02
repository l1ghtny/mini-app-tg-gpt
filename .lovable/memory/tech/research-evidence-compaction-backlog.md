---
name: Planned research evidence compaction
description: Follow safe research stopping with bounded compaction when its quality and inference costs are justified.
type: tech
---

## Decision and current behavior

On 2026-10-03 the owner approved option 1 for PR20: protect full final-answer
context space and stop research before further tool results could overfill it.
Record option 3, protocol-safe compaction of earlier research, as planned future
work. It is not implemented or activated by the current fix. Adaptive per-call
excerpt allocation (option 2) is also not part of this change; fixed evidence
limits now include provider JSON serialization.

## Why and acceptance constraints

- Support longer investigations by retaining concise relevant facts and source
  identifiers while removing repetitive older research material.
- Prefer inexpensive deterministic deduplication where sufficient. Trigger any
  inference-backed compaction only near a context boundary or when measurable
  reuse makes it worthwhile; do not pay for a compulsory pass on every request.
- Price each pass before dispatch. Count successful, failed and unknown supplier
  usage in the existing request ledger, per-task supplier ceilings and global/
  user exposure controls. Preserve final-answer funding and customer charge caps.
- Define a bounded number of passes and an explicit inference budget. Never
  create a recursive summarization/retry loop; if compaction cannot fit or fails,
  fall back to the safe research stop without retrying tools or image side effects.
- Preserve source URLs/filenames, exact material facts, uncertainty and unresolved
  gaps. Do not turn a summary into invented evidence or silently erase relevant
  contradictions. Preserve access to original evidence for verification.
- Keep call/result pairs valid and Claude signed thinking/system/schema contracts
  intact. Replaying full history must not double-decrement native task budgets;
  any supported compaction adjustment needs provider-specific acceptance tests.
- Benchmark useful-answer completion, citation fidelity, latency and total
  supplier cost including compaction, retries and refunded failures. Show that the
  improvement is worth its inference cost before enabling it.

Next: design the bounded compaction policy and its cost/evidence benchmarks after
the current early-stop implementation is reviewed. No new funding, provider
activation or automatic compaction is authorized by this backlog entry.
