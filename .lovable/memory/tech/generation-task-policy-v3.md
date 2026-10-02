---
name: No-hold task accounting and iterative research
description: Keep persisted charging policy, owner fencing and supplier exposure separate from customer balance.
type: tech
---

New `SHARED_ALLOWANCE_GENERATION_V2_ENABLED` admissions save `no-hold-v3` and
`iterative-v3`. Historical flag name does not mean old v2 held accounting. Old
rows/saved v2 plans stay held; flag-off future requests use legacy holds.

Quote/customer maxima consume no holds. Settle known success on the original
account/period, clamped to available balance and approved maxima. Failed tasks
refund all customer usage. Unknown supplier costs remain internal exposure.

Two logical tasks per user use durable lease ownership. Every provider/batch
admission verifies the owner; cancellation/stale cleanup fences it. Transport
disconnect is not cancellation. Parallel tool batches commit atomically before
dispatch. Retain protected full final capacity and match all emitted tool calls.

Protect context independently of supplier money: count all emitted/cached/skipped
tool results in their provider input shape before dispatch, admit only fixed-size
evidence that fits with a full final answer, and stop research otherwise. No
customer balance hold or answer-cap reduction is introduced. Future bounded
research compaction and its inference-cost constraints are recorded separately
in `research-evidence-compaction-backlog.md`; that feature is not implemented.

Never deploy an old binary over live no-hold rows: it can subtract nonexistent
holds. Roll back the flag in compatible code and drain saved tasks first.
Defaults, financial scenarios and activation gates live in
`docs/operations/2026-10-02-generation-budget-v2.md`; activation remains off.
