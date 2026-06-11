---
name: TBank recurring compliance and refunds
description: Frontend must collect explicit recurring consent and use backend-owned agreement/refund endpoints for TBank subscriptions.
type: feature
---

## Context / problem

TBank recurring payments require clearer buyer-facing disclosure than the original payment UI had. Support requested:

- visible subscription amount and billing period before payment
- explicit buyer consent to recurring charges
- visible contact / feedback path for refunds and cancellation

The old agreement text lived only in frontend i18n and had already drifted from current backend refund behavior.

## Decision taken

The backend is now the source of truth for recurring-payment legal/support text and refundability checks.

Implemented endpoints:

- `GET /api/v1/payments/tbank/user-agreement`
- `GET /api/v1/payments/tbank/refund-status`
- `POST /api/v1/payments/tbank/refund-current-subscription`

Refunds are self-service only within a 24-hour window from the latest confirmed current-subscription payment.

## How to apply it in future changes

- Keep recurring-consent UX requirements in sync with TBank support guidance and the frontend handoff doc.
- Update the backend agreement text first if refund policy, support contacts, or recurring wording changes.
- Use backend refund-status data as the UI source of truth instead of computing refund windows on the frontend.
- If subscription accounting becomes period-based, add an explicit payment-to-subscription-period link before expanding renewal refund behavior.

## Constraints / gotchas

- TBank recurring enablement is still terminal-side and must be confirmed by provider support.
- The restored agreement text still needs product/legal review before wide production rollout.
- Renewal refunds currently infer the affected subscription period from the latest matching subscription payment; this is good enough for the current model but not a full accounting ledger.
