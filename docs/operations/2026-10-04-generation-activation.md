# Production generation policy activation

Owner authorized direct production activation on 2026-10-04 because beta has no users.

## Runtime change

At 2026-10-04 11:35:16 UTC, enabled `SHARED_ALLOWANCE_GENERATION_V2_ENABLED` in
production `backend-env` and restarted the API Rollout plus bot, transcription and
conversation-search workers. Compatible backend build 120 was already deployed.
Production admissions now use `no-hold-v3` and saved plans use `iterative-v3`.
Beta is not activated. Claude remains disabled. Supplier funding remains
160,000,000 units per month ($160); the global risk pool is 20% of that envelope.

Rollback: disable the flag and restart compatible workers. Allow already saved
plans to finish with their own policy. Do not roll back to a pre-policy binary.
Allowance grant upgrades are sticky: disabling the flag does not reverse them.

## Executed production acceptance

Existing owner account only; synthetic prompts and one disposable text document.
Luna ordinary reply, Terra long reply (24,000-token output profile), web search and
document search completed and persisted. Duplicate request IDs reused the same
assistant message. Disconnect/reconnect preserved the ordinary task; completed
conversation stream returned 204. Required web and document searches each had a
recorded provider tool call and a subsequent final-answer attempt.

Three simultaneous admissions returned 202, 429 `active_task_limit`, 202. Both
accepted tasks were explicitly cancelled. READ ONLY ledger verification found
six no-hold admissions with zero admission units and no customer reservations.
Four tasks completed: 46,939 shared units plus 158 Luna units. The two cancellations
had zero customer charge. Their in-flight provider attempts had unknown supplier
usage, retained as exposure rather than falsely reported as free or retried.

Known cap-recovery and provider-failure handling were covered by the release's
automated checks; this probe did not deliberately exhaust a paid output cap or
re-enable Claude. Successful completion is not a general quality benchmark.

## Announcement and version gate

What's New: required. Increased allowance, ordinary sends without worst-case
confirmation, iterative research, larger answer profiles and two-task concurrency
are available in production. One bilingual shared-feed item, stable ID
`2026-10-04-generation-policy`, is published through migration `xw0e1f2a3b64`.
Backend version: 2.1.0 -> 2.1.1, operational activation/announcement of capability
whose implementation already shipped in 2.1.0. Frontend version: 2.1.1 unchanged.
The single migration history must be carried into beta without another notice.

## Follow-up

Monitor production completion, latency, failed/refunded supplier spend, unknown
exposure and guard interruptions for 48 hours. Keep inference-backed compaction
deferred. Reconcile the UX backlog and choose the next batch after this window.
Frontend received-release/source-map evidence remains a separate verification
task; a bundle release string alone is not delivery proof.
