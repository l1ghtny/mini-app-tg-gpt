# Trust, legal, and launch readiness

This is a product-readiness checklist, not legal advice. A qualified lawyer should review the final Russian documents, operator structure, payment terms, consumer rights, personal-data processing, and any cross-border provider disclosures.

## Trust questions the product must answer

Before uploading a work document or paying, a user should be able to learn:

- who operates the service and how to contact them;
- which payment entity processes the purchase;
- what a plan or top-up provides, when it expires, and whether it renews;
- which AI providers may receive prompts or files;
- where data is stored and for how long;
- how conversations, files, and accounts can be deleted;
- whether uploaded files train any model, based on actual provider terms and contracts;
- what happens during model/provider outages;
- how refunds and billing disputes are handled;
- whether the web and Telegram identities are linked and how to reverse that link.

## Public document set

### Required for launch

- Privacy policy.
- Terms of service.
- Public offer or applicable purchase agreement.
- Refund and cancellation policy.
- File-processing and retention explanation.
- Cookie and analytics disclosure where applicable.
- Operator and contact details.
- Support process and expected response window.
- Account-linking, account deletion, and data-export instructions.

### Credibility additions

- Security overview written from verified controls.
- Service status and incident history.
- Model/provider status page.
- Changelog.
- Responsible-use and acceptable-use policy.
- Subprocessor/provider list if appropriate.

## Claims that require verification

Do not publish these merely because competitors do:

- “Works without VPN” in every region or network.
- “Data stays in Russia.”
- “We do not store prompts or files.”
- “End-to-end encrypted.”
- “Your data is never used for training.”
- “All models are always available.”
- “Unlimited” without plainly disclosed fair-use limits.
- user, review, response-time, or accuracy numbers that are not measured.

Maintain a claim register containing the exact wording, evidence, owner, last verification date, and affected pages.

## Browser launch gates

### Identity and session security

- Browser authentication is deployed and tested across supported browsers.
- Production CORS, callback origins, SMTP/email delivery, and recovery flows are configured.
- Browser sessions use an appropriate hardened cookie/token strategy.
- Rate limiting, verification expiry, replay protection, and account enumeration risks are tested.
- Telegram linking handles conflicts, duplicate identities, unlinking, and recovery safely.

### Billing

- Public prices match backend entitlements and payment checkout totals.
- Receipts, recurring-charge consent, renewal, cancellation, refunds, failed payments, and support escalation are tested.
- Idempotency prevents double charging or duplicate billable work.
- Usage balances and expiration rules are visible to the user.

### Files and AI providers

- File size/type limits, scanning posture, retention, deletion, and indexing behavior are documented.
- Provider routing is logged sufficiently to answer user and incident questions without leaking sensitive content.
- Errors do not silently consume entitlement when no billable result was produced, according to the defined accounting policy.
- Citation and generated-content limitations are visible for high-risk workflows.

### Reliability and support

- Streaming reconnect and reconciliation work in the browser.
- Sentry and structured logs cover authentication, billing, streaming, and provider failures.
- A rollback/canary procedure exists for the web release.
- Support can identify a transaction or request without asking users to disclose passwords or sensitive documents.
- Status communication has an owner.

## Trust presentation

Trust should not be confined to footer links. Put the most relevant statement beside the action that creates concern:

- file handling beside upload;
- cost and balance beside generation;
- renewal beside purchase;
- provider/source information beside a result;
- account-linking consequences before confirmation;
- deletion controls in settings.
