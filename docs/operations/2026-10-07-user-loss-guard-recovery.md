# Audited account loss-guard recovery

Support can now forgive explicitly selected historical failed tasks in one user's
personal loss guard without altering customer allowance, supplier records or global
spending protection. Use `allowance_tasks.reset_user_loss_guard(session, user_id,
operation_id)` through trusted operator access and commit once. A stable operation
ID prevents repeat execution from forgiving subsequent failures. The helper uses
the same PostgreSQL advisory lock as admission and settlement. It creates one
zero-unit `user_loss_reset` event plus zero-unit `user_loss_waived` events for each
previously unwaived failed request. Active tasks and successful tasks are excluded.

Before writing, verify exact user identity, preview failed requests and known versus
unknown supplier exposure, and record the owner's authorization and reason in the
incident report. After writing, read back the events, personal loss, original
provider data, unchanged allowance and global guard totals. Do not reconcile unknown
supplier usage to zero merely to recover access. No public reset API was added.

Version gate: backend2.5.2 ->2.5.3, compatible support-recovery fix; frontend2.5.0
unchanged. Preceding production135/10325 completed all four jobs before this release.
What's New: not required. This adds an internal audited support action and executes
one owner-authorized account recovery; it does not change ordinary plan terms,
generation behavior, or public product workflows. No announcement migration and no
schema migration are required; existing allowance events provide the audit record.

Validation:60 guard/accounting/provider checks and26 owner-dashboard checks passed
on disposable local PostgreSQL. Covered known and unknown historical failures,
unchanged global financial exposure, still-enforced global guard, personal scope,
active/new failures, reset idempotence and transaction rollback. Existing automatic
image behavior is preserved by rebasing onto PR38 before publishing.

Follow-up before public launch: classify platform versus user-caused failure loss,
reconcile aged unknown usage, provide actionable guard-error recovery, add operator
preview/reason/authorization and denial alerts, benchmark the global locked scan.
The detailed owner review remains in the primary workspace's
`docs/operations/2026-10-07-generation-guard-launch-review.md`. Beta readers need the
same waiver support before relying on a production-created waiver in beta.
