---
name: TBank local webhook testing
description: Binding can be polled locally, but final TBank payment confirmation still depends on the webhook updating backend state.
type: tech
---

## Context / problem

The backend now uses a bind-first recurring flow for TBank:

- card binding via `AddCard` / `GetAddCardState`
- SBP account binding via `AddAccountQr` / `GetAddAccountQrState`
- bound charge activation via `Init` plus `Charge` or `ChargeQr`

This makes local testing asymmetric. Binding status can be refreshed by polling TBank from the backend, while final payment confirmation is still driven by the webhook handler.

## Decision taken

Treat local recurring-payment testing as two separate capabilities:

- binding UX can be tested locally with backend polling only
- final subscription activation requires the TBank webhook to hit the backend

## How to apply it in future changes

- When documenting or testing recurring payments, distinguish binding completion from payment confirmation.
- If frontend QA is running against localhost, provide either:
  - a temporary public tunnel to `/api/v1/payments/tbank/webhook`, or
  - a safe way to replay a signed webhook payload into the local backend
- Do not promise a full end-to-end recurring activation test on plain localhost without webhook delivery.

## Constraints / gotchas

- `GET /api/v1/payments/tbank/status/{payment_id}` reads persisted backend state only; it does not query TBank live.
- The subscription stays inactive until the webhook moves the payment to `CONFIRMED`.
