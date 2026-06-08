# Frontend Handoff: Release-Readiness Backend Changes

Updated: 2026-06-08

This note lists the backend changes the frontend needs to integrate for the release-ready flow.

## 1. Payments and subscriptions

### New subscription flow

The recommended subscription flow is now bind-first, then charge.

Current release posture:

- Payment buttons should now work as intended for the release candidate.
- After the user completes the payment/binding step, frontend should show a clear waiting state until the bank confirms the payment and the subscription becomes active.
- This release is intended to be shown to T-Bank reviewers as the compliant recurring-payment flow.

1. `POST /api/v1/payments/tbank/bind-init`
2. Poll `GET /api/v1/payments/tbank/bind-status/{binding_id}`
3. When the binding becomes active, call `POST /api/v1/payments/tbank/activate-bound`
4. Poll `GET /api/v1/payments/tbank/status/{payment_id}` until confirmed

### Binding init request

`POST /api/v1/payments/tbank/bind-init`

```json
{
  "tier_name": "Advanced",
  "email": "user@example.com",
  "method_type": "card"
}
```

`method_type`:
- `auto`
- `card`
- `sbp`

For SBP, `bank_id` may also be sent.

### Binding init response

Card binding returns a redirect URL:

```json
{
  "binding_id": "uuid",
  "status": "pending",
  "method_type": "card",
  "payment_url": "https://..."
}
```

SBP binding returns QR-compatible payload fields:

```json
{
  "binding_id": "uuid",
  "status": "pending",
  "method_type": "sbp",
  "qr_payload": "...",
  "qr_image_svg": null
}
```

Important:
- Binding does not activate the subscription.
- Binding does not create a paid subscription by itself.

### Binding status response

`GET /api/v1/payments/tbank/bind-status/{binding_id}`

```json
{
  "binding_id": "uuid",
  "status": "active",
  "method_type": "card",
  "payment_method_id": "uuid",
  "error_code": null,
  "error_message": null
}
```

Expected `status` values:
- `pending`
- `active`
- `failed`
- `cancelled`

### Charge from bound method

`POST /api/v1/payments/tbank/activate-bound`

```json
{
  "tier_name": "Advanced",
  "email": "user@example.com",
  "binding_id": "uuid"
}
```

Alternative request:

```json
{
  "tier_name": "Advanced",
  "email": "user@example.com",
  "payment_method_id": "uuid"
}
```

Response:

```json
{
  "payment_id": "uuid",
  "status": "NEW",
  "subscription_status": "pending_confirmation"
}
```

Frontend messaging requirement after `activate-bound`:

- Treat `subscription_status="pending_confirmation"` as an expected interim state, not as an error.
- Show a user-facing message such as: `Payment received. Subscription will be activated after bank confirmation.`
- Keep polling `GET /api/v1/payments/tbank/status/{payment_id}` until the payment becomes `CONFIRMED` or reaches a terminal failure state.
- For this release, do not imply that the subscription is active immediately after the button press; activation happens only after bank confirmation.

### Compatibility note for old endpoint

`POST /api/v1/payments/tbank/init` still exists, but it is now only a compatibility alias to binding-init.

Do not use it as a one-step “start paid subscription now” flow anymore.

### Saved payment methods

New endpoints:

- `GET /api/v1/payments/tbank/payment-methods`
- `POST /api/v1/payments/tbank/payment-methods/{payment_method_id}/default`
- `DELETE /api/v1/payments/tbank/payment-methods/{payment_method_id}`
- `POST /api/v1/payments/tbank/retry-renewal?payment_method_id=...`

Saved method response shape:

```json
{
  "id": "uuid",
  "type": "card",
  "status": "active",
  "is_default": true,
  "card_type": "Visa",
  "pan": "**** 4242",
  "exp_date": "1229",
  "phone": null,
  "bound_at": "2026-06-04T11:00:00",
  "detached_at": null,
  "last_charge_at": "2026-06-04T11:10:00",
  "last_charge_status": "processing",
  "last_charge_error": null
}
```

Method `status` values:
- `pending`
- `active`
- `detached`
- `failed`

Detach semantics:
- Detach does not delete the method row from history.
- Detach removes it from active renewal use.

### Active subscription payload changes

`GET /api/v1/user/subscription/active`

Each active subscription now includes:
- `renewal_state`
- `renewal_grace_until`
- `last_renewal_attempt_at`
- `last_renewal_failure_reason`
- `default_payment_method_id`

`renewal_state` values currently returned by backend:
- `inactive`
- `scheduled`
- `requires_method`
- `grace`
- `disabled`

`last_renewal_failure_reason` values to handle:
- `missing_method`
- `detached_method`
- `expired_method`
- `declined`
- `insufficient_funds`
- `provider_error`

UI implications:
- `cancel subscription` now disables auto-renew only.
- It no longer deletes saved methods.
- A subscription may remain active during grace.

### Retry renewal

Manual recovery path:

`POST /api/v1/payments/tbank/retry-renewal`

Optional query parameter:
- `payment_method_id`

Use this after:
- user binds a new method
- user switches default method
- user fixes a previously failing method

### Usage-pack flow

Usage-pack payment flow is unchanged:
- `POST /api/v1/payments/tbank/init-usage-pack`

### Recurring-payment compliance requirements

To satisfy T-Bank recurring-payment review, frontend must add all of the following before the bind/init payment action:

- Show the exact subscription amount before payment.
- Show the billing period / recurrence clearly, for example `999 RUB every 30 days` or `999 RUB monthly`.
- Show an explicit consent control for recurring charges.
- The consent control must be unchecked by default and must be actively confirmed by the user.
- Keep support / refund / cancellation contact information visible from the payment surface.
- Provide a visible path to cancel auto-renew and a visible support contact for refund questions.

Recommended UX copy requirements:

- The primary CTA should stay disabled until the user confirms recurring billing consent.
- Next to the CTA, show a short consent line such as: `I agree to recurring monthly charges until I cancel auto-renew.`
- Link the consent line to the public-offer / subscription-terms text.
- Keep a support contact near the paywall or payment status UI: `support@lightny.pro`.

New backend endpoint for agreement text:

- `GET /api/v1/payments/tbank/user-agreement`

Response shape:

```json
{
  "document_key": "public_offer",
  "version": "2026-06-08",
  "lang": "ru",
  "title": "Публичная оферта и условия подписки",
  "text": "..."
}
```

Frontend should use this endpoint as the source of truth for the agreement / offer modal instead of keeping a stale local copy.

### Refund UI requirements

New backend endpoints:

- `GET /api/v1/payments/tbank/refund-status`
- `POST /api/v1/payments/tbank/refund-current-subscription`

`GET /refund-status` response:

```json
{
  "refundable": true,
  "reason": null,
  "window_hours": 24,
  "payment_id": "uuid",
  "tier_name": "Advanced",
  "amount_cents": 99900,
  "purchased_at": "2026-06-08T10:15:00",
  "refund_deadline_at": "2026-06-09T10:15:00"
}
```

Current `reason` values:

- `no_active_subscription`
- `no_subscription_payment`
- `payment_not_confirmed`
- `already_refunded`
- `window_expired`

`POST /refund-current-subscription` response:

```json
{
  "payment_id": "uuid",
  "status": "REFUNDED",
  "subscription_status": "cancelled",
  "refunded_at": "2026-06-08T10:30:00"
}
```

UI behavior:

- Only show the self-service refund CTA while `refundable=true`.
- Show the cutoff time from `refund_deadline_at`; do not calculate it locally.
- After a successful refund, remove paid-subscription access UI and refresh `GET /api/v1/user/subscription/active`.
- If `window_expired`, route the user to support instead of showing a direct refund action.


## 2. Daily free-tier usage

Daily resets now use UTC calendar midnight, not rolling 24h.

Frontend should trust backend-provided `next_reset_at` and not calculate reset times locally.

### Text usage changes

`GET /api/v1/user/usage/me/models`

OpenAI and Google mirrored text models are now returned as shared usage buckets instead of separate rows.

Current shared text buckets:

- `gpt-5.4-nano`
  - `gpt-5.4-nano`
  - `gemini-3.1-flash-lite`
- `gpt-5.4-mini`
  - `gpt-5.4-mini`
  - `gemini-3.5-flash`
- `gpt-5.5`
  - `gpt-5.5`
  - `gemini-3.1-pro-preview`

Unpaired models continue to appear as their own rows.

`TextEntitlementEntry` now includes:
- `next_reset_at`

`TextModelUsage` now includes:
- `display_name`
- `display_name_ru`
- `bucket_models`

Example:

```json
{
  "model": "gpt-5.4-nano",
  "display_name": "Fast",
  "display_name_ru": "Быстрый",
  "bucket_models": ["gpt-5.4-nano", "gemini-3.1-flash-lite"],
  "total_remaining": 4,
  "selected": {
    "kind": "tier",
    "source": "free",
    "cap": 5,
    "used": 1,
    "remaining": 4,
    "next_reset_at": "2026-06-05T00:00:00"
  }
}
```

Notes:
- Free nano-tier text access is now daily-governed.
- Premium sample access remains finite and separate from daily free access.
- `next_reset_at` is only present where daily reset behavior applies.
- Frontend should stop assuming there is one usage row per raw model name.
- Frontend should treat `model` as the canonical shared bucket id and `bucket_models` as the concrete selectable models inside that bucket.
- Frontend should render row titles from `display_name` / `display_name_ru` instead of raw `model` ids.
- Consumption is shared across every model listed in `bucket_models`; if a user spends one request on any member, the bucket's `used` value increases for the whole row.
- If the UI still exposes raw model pickers, it should map those raw model names back to the single returned bucket row instead of expecting duplicate usage entries.

### Image usage changes

The following endpoints may now include `next_reset_at` on daily-governed entries:

- `GET /api/v1/user/usage/me/image-models`
- `GET /api/v1/user/usage/me/image-energy`

Do not assume every source has a reset timestamp.


## 3. Google image pricing and UI keys

Google image billing is now canonicalized to size-based keys only.

Applies to:
- `GET /api/v1/models/catalog`
- `GET /api/v1/tiers`
- `GET /api/v1/tiers/{tier_id}`
- `GET /api/v1/user/usage/me/image-models`

For Google image models, frontend should only expect:
- `512`
- `1k`
- `2k`

Frontend should stop expecting Google image options such as:
- `low`
- `medium`
- `high`

OpenAI image quality behavior is unchanged.

Reference table: [docs/google-image-pricing.md](G:/0/Coding_projects/Python/PycharmProjects/mini-app-tg-gpt/docs/google-image-pricing.md)


## 4. Documents and provider-aware state

### User settings

`GET /api/v1/user/settings`
`PUT /api/v1/user/settings`

New field:

```json
{
  "default_document_provider": "openai"
}
```

Accepted values:
- `openai`
- `google`

### Upload document

`POST /api/v1/documents/upload?provider_override=openai`

`provider_override` is a query parameter.

If omitted, backend resolution order is:
1. request override
2. user `default_document_provider`
3. backend default

### Document list response changes

`GET /api/v1/documents`

Each document now includes:
- `primary_provider`
- `provider_artifacts`

Example:

```json
{
  "id": "uuid",
  "filename": "notes.txt",
  "status": "ready",
  "primary_provider": "openai",
  "provider_artifacts": [
    {
      "provider": "openai",
      "status": "ready",
      "external_file_id": "file_...",
      "external_index_id": "vs_...",
      "error_code": null,
      "error_message": null,
      "indexed_at": "2026-06-04T11:00:00"
    }
  ]
}
```

Artifact `status` values:
- `uploading`
- `processing`
- `ready`
- `failed`
- `delete_queued`
- `deleted`

Frontend should render provider readiness from `provider_artifacts`, not from local assumptions.

### Conversation document attach/list changes

`PUT /api/v1/conversations/{conversation_id}/documents`

Request body now supports:

```json
{
  "document_ids": ["uuid"],
  "provider_override": "openai"
}
```

Response now includes:

```json
{
  "conversation_id": "uuid",
  "document_ids": ["uuid"],
  "effective_provider": "openai"
}
```

`GET /api/v1/conversations/{conversation_id}/documents`

Supports optional query parameter:
- `provider_override`

Also returns `effective_provider`.

Important:
- Frontend must use backend-returned `effective_provider`.
- Do not assume requested provider == effective provider.

### Current rollout behavior

The contract is live, but document-provider rollout is still backend-guarded.

Current safe assumptions for frontend:
- OpenAI document retrieval remains the working baseline.
- If Google is unavailable, backend can fall back to OpenAI and will return `effective_provider: "openai"`.
- Frontend should handle provider artifacts generically and tolerate failed/non-ready providers.


## 5. Frontend implementation checklist

- Switch subscription purchase UX to bind-first, then charge.
- Add payment-method management UI around `payment-methods`, `set default`, `detach`, and `retry-renewal`.
- Update subscription UI to show `renewal_state`, `grace`, and failure reasons.
- Treat cancel as “disable auto-renew”, not “remove payment method”.
- Read `next_reset_at` from usage responses and display UTC-reset countdowns from backend data.
- Update Google image resolution/quality UI to use only `512`, `1k`, and `2k`.
- Add `default_document_provider` to user settings state.
- Pass `provider_override` where document-provider selection is user-driven.
- Use `effective_provider` from conversation-document responses.
- Render document indexing readiness from `provider_artifacts`.
