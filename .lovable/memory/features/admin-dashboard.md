---
name: Owner reporting dashboard
description: Read-only admin reporting, accounting sources and rollout access
type: feature
---

Owner authorized read-only overview, ranked users/detail, purchases and effective
settings/pricing. Backend `/api/v1/admin/dashboard/*`, frontend `/admin/usage`.
Normal login plus explicit server allowlists; empty lists deny all. No broadcast
secret fallback. Configure owner/test identities via backend environment, not Vite.

Customer parent debits, supplier child estimates, unresolved exposure and payment
amounts are separate. Beta/prod shared data is counted once. Do not assume token
estimates equal invoices, current entitlements prove payment fulfillment, or lazy
uninitialized grants are zero. Mark historical/audio coverage gaps explicitly.

No mutations or chat/payload/credential views. Page CSV only in browser. What's New
not required for internal owner-only reporting. See docs/operations/2026-10-04-admin-dashboard.md
for authoritative metric definitions, tests and rollout instructions. Audited writes
and reconciliation are future scope, not implied authorization.
