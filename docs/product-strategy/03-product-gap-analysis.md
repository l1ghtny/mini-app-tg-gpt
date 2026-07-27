# Product gap analysis

## Existing foundation

The current product should be presented as more than a chat wrapper. The repositories indicate support or foundations for:

- multiple GPT, Gemini, Perplexity, and image models;
- quick, standard, and deep search modes plus URL retrieval;
- documents, attachments, file search, and image generation;
- conversations, folders, chat search, editing, regeneration, deletion, sharing, and personalization;
- subscriptions, usage packs, discounts, and entitlement-aware limits;
- resumable streaming, request idempotency, Sentry, and canary rollouts;
- Telegram authentication plus an in-progress browser authentication foundation.

Every public statement must reflect production behavior, not code that is merely present or awaiting deployment.

## P0: required before a serious browser launch

| Gap | Why it matters | Done when |
| --- | --- | --- |
| Browser authentication | The web app cannot be primary if login, recovery, and callback handling are fragile | Email and/or identity-provider login works in production; sessions use hardened cookie handling; recovery and logout are tested |
| Web–Telegram account linking | Cross-surface continuity is the main strategic advantage | A user can link, unlink, and recover accounts without duplicate histories or unsafe merges; the flow is covered by E2E tests |
| Public pricing and catalogue | Buyers need value and price before committing personal data | Current plans, model access, limits, renewals, and top-ups are viewable without authentication and are cacheable |
| First-value experience | Forced registration before proof suppresses conversion | A safe guest demo or free first task demonstrates a real outcome and clearly explains when signup is required |
| Trust and policy surfaces | Files, payments, and AI providers create material buyer concerns | Privacy, terms, offer, refunds, file processing, deletion, contacts, and support routes are live and consistent |
| Production analytics | The team cannot improve what it cannot observe | The full acquisition-to-payment funnel is measured with consent-aware analytics and backend events |
| Catalogue text integrity | Corrupted model copy damages trust and SEO | Broken Sonar fallback strings in `app/services/model_registry.py` and any similar encoding defects are fixed and tested |

## P1: complete the focused aggregator promise

### Automatic model recommendation

Ask for the task, recommend a model/mode, explain the trade-off, and show which request pool it will use. Preserve manual selection for expert users. Start with deterministic rules and measured outcomes before building an expensive learned router.

### Side-by-side comparison

Let users send one prompt to two or three models, compare outputs, and continue from the preferred result. Clearly show that multiple requests consume multiple allowances.

### Claude integration

Claude is the first recommended catalogue expansion because Russian buyers recognize it and competitors treat it as a core provider. Confirm commercial availability, billing, moderation, and data-processing implications before announcing it.

### Serious document workflows

- citations that point to the exact file and page;
- OCR and table-extraction status, with explicit uncertainty;
- multi-document collections or projects;
- source snippets and citation navigation;
- export to Markdown, DOCX, and PDF;
- clear limits for type, size, retention, and indexing time.

### Transparent hybrid payments

Keep subscriptions for predictable recurring use and offer top-ups for peaks. Sell top-ups as additional requests for a named model group, with a clear expiration date and no token conversion. Avoid a universal credit system that users cannot mentally convert to outcomes.

### Prompt and result handoff

SEO examples and templates should open the app with the selected prompt or workflow prepared. Users should be able to save a workflow, rerun it with new inputs, export the result, and share it with privacy controls.

## P2: retention and expansion

- referral rewards tied to activated or paying users, with abuse controls;
- lifecycle messages for unfinished onboarding, completed long tasks, allowance thresholds, and renewals;
- personal prompt/workflow library;
- project-level memory and reusable document sets;
- status page, changelog, model availability and latency history;
- DeepSeek or Qwen as the next recognized cost-efficient provider;
- team workspaces, administrative controls, and invoicing after individual demand is proven;
- public API after reliable metering, quotas, documentation, and support are ready;
- image editing, references, inpainting, and background tools if usage data supports them.

## Explicitly later

- A video/music generation arms race.
- Integrating providers primarily to increase a model-count badge.
- Complex autonomous agents before simple workflows have strong activation and retention.
- Enterprise promises without security documentation, access controls, support capacity, and contracts.

## Product principles

1. Optimize for completed work, not message count.
2. Reveal complexity gradually; recommend first, expose controls second.
3. Make price and data movement understandable before the user commits.
4. Treat citations and files as product objects, not text decorations.
5. Preserve resilience, idempotency, entitlement checks, and observability while improving UX.
6. Do not promise seamless cross-surface history until linking is production-verified.
