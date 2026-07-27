# Metrics and experiments

## North-star direction

Track **weekly activated users who complete a valuable task**, segmented by workflow and acquisition source. A valuable task is not merely sending a message; it is a completed result such as a sourced research answer, successful document analysis, export/share, or a multi-turn work session.

Define this event precisely in analytics before using it as a target. Keep paid and unpaid cohorts separate.

## Funnel

| Stage | Core metric | Diagnostic metrics |
| --- | --- | --- |
| Discovery | Qualified organic and referral sessions | Search impressions, ranking distribution, CTR, branded/non-branded split |
| Landing | Landing-to-primary-CTA rate | Proof interaction, pricing views, Telegram-secondary CTA, bounce by intent |
| Authentication | Successful auth rate | Start-to-complete, callback errors, email delivery, account-link conflicts |
| Activation | First valuable task completed | Time to first token, time to result, failure rate, guest-to-account conversion |
| Retention | D7 and W4 activated retention | Returning workflow, cross-surface use, saved projects, document/search adoption |
| Monetization | Activated-to-paid conversion | Pricing-to-checkout, payment success, plan mix, top-up rate, cancellation |
| Economics | Contribution margin per active and paid user | Inference cost, provider/model mix, support/refund cost, discounts |
| Reliability | Successful task rate | Stream recovery, provider error, latency percentiles, duplicate-charge incidents |

## Product segment metrics

- Research: source opening rate, citation coverage, corrected/flagged citations, export/share rate.
- Documents: upload success, indexing time, cited-page navigation, multi-document retention, deletion success.
- Model guidance: recommendation acceptance, override rate, completed-task rate and cost versus manual selection.
- Comparison: compare-session completion, chosen-answer continuation, incremental cost, conversion impact.
- Cross-surface: verified link rate, web-to-Telegram and Telegram-to-web continuation, duplicate-account incidents.
- Payments: allowance comprehension, balance-related support contacts, renewal success, refund and chargeback rate.

## Event design principles

- Use a stable anonymous ID before authentication and reconcile it safely after login.
- Carry source, campaign, landing route, and prompt-template attribution into the app.
- Record outcome and error category without sending prompt or document content to analytics by default.
- Use backend events for authoritative payment, entitlement, and completed-task state.
- Version important experiments and page variants.
- Document event names, properties, consent rules, owner, and retention.

## First experiment backlog

### Message and proof

1. Research-with-sources hero versus all-in-one-workspace hero.
2. Real workflow recording above the fold versus static interface image.
3. Price/free allowance in the hero versus immediately below proof.

### Friction

4. Guest first task versus signup-first.
5. Email/Yandex/Telegram login choice order.
6. SEO prompt prefilled in the app versus generic new chat.

### Product guidance

7. Recommended model selected by default versus manual catalogue.
8. Pre-request ruble estimate versus internal credit estimate.
9. Two-model comparison offered after an unsatisfactory result versus before sending.

### Monetization

10. Subscription-led pricing versus subscription plus clearly priced top-up.
11. Outcome-based plan comparison versus feature/credit table.
12. Annual discount only after activation versus on the first pricing visit.

## Experiment rules

- State a primary metric and guardrails before launch.
- Do not test security, consent, legal disclosure, or deceptive scarcity away.
- Run an experiment long enough to cover weekly behavior and payment cycles where relevant.
- Segment new versus returning, paid versus free, and high-cost workflows.
- Ship a winner only when the effect is commercially meaningful and does not damage retention, margin, reliability, or support burden.
- Keep a decision log, including inconclusive and losing tests.

## Initial target-setting

Do not invent industry benchmarks as goals. Measure a two- to four-week baseline, identify the largest verified funnel loss, and set the next target as a realistic relative improvement. The first objective is instrumentation quality and successful-task definition; conversion optimization follows.
