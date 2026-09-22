---
name: Subscription-aligned allowance periods
description: Local implementation of anniversary accounting and fixed-term invitation allowances
type: feature
---

Implemented locally, not deployed: private shared-allowance grants follow subscription activation and access expiry. Short invitations up to 31 days get one grant; ongoing access uses calendar-month anniversaries. Highest overlapping private capacity wins without refilling spend, and existing active cycles survive tier changes.

`subscription_anchor` marks aligned accounts. Legacy alignment keeps the same account ID and balances with a zero-unit audit event. Multiple overlapping legacy rows require reconciliation. Both prod and beta share the same scope; coordinate the backend cutover so old calendar writers cannot issue extra grants. Supplier operational budgets stay calendar-based.

Frontend must distinguish `resets_at` from `period_end_kind=access_expiry` and `access_expires_at`. Trial lifetime and payment availability are unchanged. Details and release requirements: `docs/operations/2026-09-22-subscription-allowance-periods.md`.
