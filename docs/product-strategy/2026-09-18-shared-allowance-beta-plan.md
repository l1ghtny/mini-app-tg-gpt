# Shared monthly AI allowance: beta implementation plan

Status update, 20 September: implementation and local validation are recorded in `2026-09-20-shared-allowance-validation.md`; beta deployment remains pending. The original deadline below has passed. Current rollout order is in `../operations/shared-allowance-beta.md`.

Prepared 18 September 2026. Target: private friend feedback on beta on **19 September 2026**, Europe/Bratislava. This is an implementation plan, not a report of completed or deployed code. The date is a delivery target subject to the acceptance checks below.

## Product decision and scope

The user approved the direction of a simple usage meter backed by variable usage accounting. This supersedes the previous four independent reply quotas for the replacement catalog. Keep four model groups for navigation, with the agreed lineup: Luna; Terra/Sonnet 5; Sol/Opus 5; Astra/Fable 5.1. Keep Flare as a chat image tool. The owner confirmed there are no paying customers and all private-tier usage is owner-funded. Replace the existing capacity rules directly; no grandfathered subscriptions, legacy pricing screen, benefit-conversion calculation or opt-in switch is required. Redesign private-tier capacity after the public plans, then migrate those users to the same allowance engine. Preserve accounts, conversations and historical accounting. Work is excluded.

Beta recommendation: a single monthly paid AI allowance covers counted-model chat, search and image generation. Luna text has separate included fair use; billable search/image actions still use the paid allowance. Do not add the earlier fixed Flare image grants on top: that would double-fund benefits. Display tool consumption where a user selects the tool. This packaging replaces old reply/image quotas at cutover; do not carry old balances forward as additional grants.

A private beta grant should exercise real deductions and exhaustion without requiring the friend to purchase a subscription. A temporary beta grant can replace their old capacity for testing while the final private-tier amounts remain undecided. Record it as a distinct, expiring beta grant. Its monthly-equivalent capacity is visible; a test refill is an auditable grant, never an edit that erases history. The cohort account identifier must be supplied/selected before enabling it; do not infer another person's identity from old usage reports.

## Communicating value

Recommend **1x / 2x / 5x / 20x the monthly AI allowance of Start**, with candidate prices 490 / 990 / 2490 / 9990 RUB. Start is the lowest PAID baseline. Free/Luna fair use is not a meaningful common denominator. Multiples describe capacity at identical customer debit rates and billing duration, not answer quality, speed, price savings, context length or total product value.

| Candidate plan | Monthly price | Displayed allowance | Positioning direction |
|---|---:|---:|---|
| Start | 490 RUB | Base allowance | Occasional personal tasks |
| Plus | 990 RUB | 2x Start | Regular writing and research |
| Premium | 2490 RUB | 5x Start | Frequent work and flagship access |
| Max | 9990 RUB | 20x Start | Sustained, demanding use |

These are design candidates. Audience descriptions are hypotheses to test, not verified capacity claims. Preserve Astra/Fable as upper-plan access for the initial commercial proposal, independently of the allowance multiplier; internal beta test grants may permit all seven. No model is advertised as usable before its real adapter/capabilities pass acceptance.

The main card shows price, a short use-case line, the allowance multiple and model access. Common benefits appear once: Luna, personalisation, projects and available document features. Keep three ordinary cards with a compact Max option rather than four equally dense blocks. Current subscription is a separate compact summary. Receipt email belongs after plan selection. Storage/retention details stay available in a touch/keyboard-accessible disclosure.

Multiples help customers compare plans but do not explain the base plan by themselves. Add a single shared “What does this cover?” section using repeatable example tasks: rewrite a message, draft a brief, compare sources and edit an image. Show sample outputs and approximate percentage consumption for a stated model/settings version after the actual adapter replay. Do not rebrand these as guaranteed monthly reply counts. Later, observed account history can support an explicitly estimated plan recommendation. No invented Most popular badge or unmeasured 2x/5x value claim.

Daily usage experience: one percentage meter, exact renewal date and optional per-task history. Resolve amount = granted minus settled consumption minus active reservations. Refresh after completion, cancellation and errors; keep in-progress reservation distinct so a refund does not appear to be an unexplained balance increase. Percentage precision should remain readable at low balance. Every paid tier starts at 100%, so a tier's base-relative multiplier must remain visible in plan comparison.

Warn before unusual expenditure, not before every ordinary message. Show an estimate range and allow an explicit spend ceiling for the request; estimates are not guaranteed final cost. Provide actionable low/exhausted states: use included Luna text, wait for renewal or view plans. No automatic purchase, upgrade or model substitution. Monthly allowance reset initially; no additional five-hour/weekly customer-facing quota windows. Technical rate limits and Luna fair use remain separate, disclosed policies.

## Internal economics: a coherent beta configuration

Use one integer accounting unit, with no floats. Proposed canonical unit is one millionth of a US dollar of standard-price usage under an immutable customer rate-card version; this is internal accounting, not a money wallet or redeemable cash balance. Keep actual supplier cost, invoices/incentives and customer debits separate. Rates need not equal cash invoices, and no temporary supplier subsidy funds the grant.

A conservative starting candidate is a base allowance equivalent to **USD 1.25 of published standard-rate paid usage**, with 1/2/5/20 multiples. At planning FX 100, reserve another 20% for failures, pricing variance and retries. Luna and internal background work remain additional separately funded reserves. Search/images are WITHIN the common paid allowance.

| Plan | Internal standard-rate grant, USD | Paid usage plus 20% reserve, RUB | Luna/background reserves, RUB | Total provider allocation, RUB | Contribution at full paid redemption | At FX 120 |
|---|---:|---:|---:|---:|---:|---:|
| Start | 1.25 | 150 | 35 + 10 | 195 | 40.1% | 32.1% |
| Plus | 2.50 | 300 | 60 + 20 | 380 | 42.6% | 34.9% |
| Premium | 6.25 | 750 | 120 + 40 | 910 | 45.2% | 37.9% |
| Max | 25.00 | 3000 | 400 + 100 | 3500 | 48.0% | 41.0% |

Includes user-confirmed 6% tax; assumed 6% all-in payment/receipt fees, 3% refund reserve and fixed overhead allocations of 25/40/80/200 RUB. These are conditional unit contributions, not company profit. Public grant sizes remain provisional: amount of useful work must be validated, Luna fair use must fit its allocation, and fees/overhead are not account-verified. Merely hiding a small grant behind 100% does not create value.

Do not automatically apply the existing 50% introductory discount to the new plans. At these grants, full redemption leaves about -12 / 1 / 68 / 546 RUB contribution at half price, below the target even where positive. Remove the old introductory-discount default from replacement offers; there are no paying subscriptions to grandfather.

A 40x Max allowance is a possible DIFFERENT margin choice, not mathematically impossible: paid allocation would be 6000 RUB plus 500 ancillary, leaving approximately 17.9% full-redemption contribution at FX 100 and 4.9% at FX 120 under these assumptions. Start at 20x for the beta comparison; do not advertise 40x without intentionally funding that tradeoff. Never double displayed multiples while retaining the same backend grant.

Calculations: `2026-09-18-shared-allowance-proposal.json` beside this plan; the original is in the local visualization economics-20260918 directory. This grant model supersedes the old reply-count candidate as the proposed architecture; the old report remains evidence about task variance and inference quality.

## Private-tier capacity redesign — after public plans

The owner funds these users and explicitly authorized their migration. Reuse the same rate card, accounting, usage meter and reset mechanics as public plans; private tiers differ through configurable allowance amount and model access, not a second quota system. Keep them out of the public pricing comparison.

After the public baseline is validated, inventory current private tiers and assign each a monthly allowance expressed internally as a multiple of Start. Set an aggregate owner-funded monthly budget, choose per-tier capacities within it, and allow explicit auditable discretionary grants. Keep development/test consumption separately identifiable. Exact private-tier capacities are not decided in this plan and must not be inferred from old reply counters or copied automatically from Max.

For migration, define the effective reset/start date, grant each user their target capacity exactly once, and disable old reply/image-energy/pack enforcement for that user in the same cutover. Retain historical usage records for analysis without translating old remaining counts into bonus capacity. A finite temporary beta grant enables tomorrow's feedback before the permanent private-tier mapping is finalized. Final private-tier capacity and migration follow the main public-plan work and precede the full catalog cutover.

## Confirmed starting point

Existing paired branch: `codex/model-pools-pricing-20260914`.
- Backend worktree: `/private/tmp/lightny-model-pools-20260914/backend`, current remote master `8f007c1fbf091a42f3b950f3c2a7e76e6d7cdf48`.
- Frontend worktree: `/private/tmp/lightny-model-pools-20260914/frontend`, current remote main `eac0402199a60493d5074a0ac3721c9afbb4c20d`.
- Read-only remote verification on 18 September matches both worktree bases. Current remote beta heads: backend `a7e87627f996db175a34a89ea0d61da8d72a8d87`, frontend `fe998fe83a964ca5547cf77fa16e9f6414590436`.
- Shared checkouts contain unrelated work and must remain untouched by implementation commits. Persist planning artifacts before temporary-directory cleanup.
- `ai_service.py` dispatches Google, Perplexity and OpenAI only. **No Anthropic adapter exists at this production base.** Direct Claude benchmark success does not establish chat streaming, images, search, cancellation or accounting integration.
- RequestLedger is request-count accounting today; extend through additive linked accounting tables rather than reinterpreting historical rows. Existing token counters distinguish visible output from reasoning; the new normalization must preserve that fact.

## Delivery sequence for tomorrow's beta

18 September implementation order: lock the additive contract and accounting semantics; implement accounting/provider work; connect the UI against the real contract. 19 September target: deploy compatible revisions, complete end-to-end and mobile/desktop acceptance, then enable the friend cohort. These are sequencing targets, not an estimate that every item fits in the remaining hours. Any incomplete provider capability is reported explicitly before feedback.

Beta must have finite expiring grants, explicit server-configured Luna fair-use limits, and an operational aggregate supplier-spend threshold. Define their numeric settings before enabling the cohort; an assumed Luna reserve in a spreadsheet is not an enforcement mechanism. Keep normal Luna access usable while preventing unbounded tools or background work.

### A. Accounting and contract first — critical path

Backend targets: `app/db/models.py`, new migration, `app/services/subscription_check/entitlements.py`, `app/services/pricing_service.py`, `app/api/chat_helpers.py`, `app/api/helpers.py`, `app/services/background/save_openai_usage.py`, `app/api/user_usage.py`, `app/api/user_usage_helpers.py`, `app/api/public_catalog.py`, `app/api/tier_helpers.py`, relevant schemas.

Add period-bound allowance accounts/grants, append-only debit/reservation/adjustment events and per-provider-attempt usage linked to RequestLedger. Snapshot plan/rate-card version, allowance account and period on request admission. Include provider request ID, model, normalized input/cache-write/cache-read/output/reasoning, tools, completion status and latency. No hidden reasoning text.

Atomic admission locks the account, checks spendable amount including concurrent reservations, and creates one reservation per client_request_id. Reserve before upstream spend, settle once after durable usage, release unused reservation. Retries, resume and duplicate callbacks cannot duplicate deductions. Usage arriving after month rollover settles against the ORIGINAL period. Unknown final usage remains a reconcilable pending item; crashes must not silently refund or double-charge it.

Customer successful-result usage and internal retry/summary/title costs have explicit policies. Our own failed attempts do not deduct repeatedly from the user; retain their supplier costs and fund them from reserves. User-cancelled partial useful generations may settle known delivered usage under a documented policy. Provider timeout with unknown usage needs reconciliation. Published-rate cache savings reduce deductions consistently. Price/rate versions are immutable for an issued period; supplier repricing triggers alerts and future version changes, not retroactive charges.

Proposed additive API contracts:
- `GET /api/v1/user/usage/me/allowance`: mode (shared/beta), grant/consumed/reserved/available integer units, display percent, period/reset, rate-card version, model access and Luna fair-use state. Avoid exposing raw supplier margins.
- Add nullable allowance metadata to catalog/tier responses: stable baseline plan/version, relative multiple, period, eligible models, purchase availability and beta status. Keep old response fields only as temporary deployment compatibility where needed, not as a separate commercial entitlement system; remove obsolete counters after coordinated cutover.
- `POST /api/v1/conversations/{id}/usage-estimate`: selected model, tools, content/attachment references and optional spend ceiling; returns estimate range, warnings, expiry and snapshot/version. Do not persist prompt text just for estimates. Reject cross-user attachments.
- Existing send endpoint accepts an optional estimate reference/spend ceiling. Revalidate atomically against changed conversation/context and current balance; estimates never authorize a higher spend after expiry. No bearer header alone grants beta access.

A simple estimate is part of beta; fully personalized usage forecasting and add-on purchases are follow-up work. Retain current message IDs, SSE events, error/reconnect behavior and idempotency.

### B. Provider and inference integration — critical path alongside A

Targets: `model_registry.py`, `ai_service.py`, `openai_service.py`, `openai_chain.py`, new Anthropic adapter and tool bridge, chat context/document retrieval helpers.

Register exact tested model IDs, provider-specific capabilities and output/reasoning policies. Implement real Anthropic text streaming, error normalization, cancellation, image/document inputs and tool-use/result cycles. Flare remains application-managed image generation, with validated ownership and edit lineage; Claude cannot call OpenAI hosted tools directly. Search needs a verified provider-appropriate bridge with sources. Test the supported capability matrix; no silent switches to OpenAI for a user who chose Claude.

Preserve medium reasoning where it demonstrated task quality. Do not globally force low, or impose the rejected 1536-token output ceiling. Sonnet's difficult benchmark required 11,838 output tokens; use a model-specific configuration and reserve its exposure, then validate completion in the app. Avoid automatic retry/escalation loops.

Account for total effective context, not just locally transmitted messages or previous_response_id payload length. Roll over chains from a durable fact summary plus recent turns and retrieved sources. Preserve corrections, citations, exact text and attachments needed for the task. Cache stable instructions. Bound tools and results, and attribute all child calls to the logical request.

Before each controllable billable step, verify reservation capacity. Customer deductions cannot exceed an approved spend ceiling. Upstream hosted tools can have uncertain final costs; do not call an estimate a guaranteed supplier-cost cap. Exposure outside controllable bounds is platform risk: instrument it, fund a beta contingency, restrict unsupported operations and use an operational spend stop. Do not truncate hidden reasoning until there is no budget left to produce an answer and then present the result as a success.

### C. Frontend: plan comparison and usable balance

Targets: `src/components/SubscriptionDialog.tsx`, `SubscriptionButton.tsx`, `ToolControls.tsx`, model picker/gating helpers, `src/lib/api.ts`, `src/types/index.ts`, store, `src/lib/refreshUsage.ts` and locales.

Implement backend-driven multipliers and meter; no model-specific monthly reply counters in shared mode. Use one replacement pricing/usage screen; no legacy subscription view is needed. Show included Luna state and the extra consumption of search/images where selected. Remember model selection; Terra remains the paid default. Keep unsupported capability combinations unavailable with a concise explanation.

Render normal/low/reserved/exhausted/pending/error states; include a brief optional task consumption history. Attach expensive-request warning and spend choice to the send flow. Refresh through the shared debounced usage helper after completion/cancellation/recovery. Replacement-plan checkout remains unavailable in the private beta; keep account settings working and enable checkout only with validated final offers. Translate production-facing Russian copy using the native Russian skill during implementation.

### D. Deploy a real private beta and collect feedback

Target all seven models plus Flare. Full Anthropic tools/images are the largest schedule risk; if a capability fails acceptance, do not fake availability or silently route it elsewhere. A smaller working capability set can test allowance UX, but report that explicitly as partial delivery; it is not completion of the agreed lineup.

Apply additive idempotent migrations once through the established master-only migration ownership, accounting for the shared beta/production database. Coordinate the compatible schema rollout before enabling beta code; never create divergent migration histories on beta. No global public tier updates or new sale offers from a seed migration. Runtime enablement requires the beta environment AND a server-side cohort/grant; production keeps the existing behavior only during staged deployment, not as a grandfathered product. At coordinated cutover, retire old quota/image-energy enforcement for migrated users. Turning the feature off must not erase accounting. Keep the implementation production-based and carry it into beta through the existing integration/release workflow without importing unrelated beta Work changes into master/main.

Use the current TeamCity/release workflow at implementation time; load its skill then. Inspect the beta integration diff, resolve conflicts narrowly, verify migration head and provider secrets via server-side configuration, and deploy paired compatible revisions. Health checks are not acceptance. Report deployed image/revision and real beta behavior separately. No public What's New/pricing announcement until production release.

Acceptance before friend access:
- A real send and streamed completion updates the same allowance once; stop/resume, retry and refresh do not duplicate it.
- Two concurrent final-balance requests cannot both spend the same funds; month rollover, stale estimate, provider failure and worker crash recover correctly.
- Real supported send/search/document/image/follow-up edit on each public beta model; uploaded media ownership and SSE recovery remain intact.
- Validated short, reasoning and 20th/40th-context cases through the ACTUAL adapter, with complete usage for tools and auxiliary work. Quality failures remain visible.
- No double charging through old quota/image-energy paths. Verify deterministic migration, exactly one new grant per user/period and no unintended conversion or stacking of old packs/balances.
- Compare receipt/invoice-reconcilable provider usage with account events. If exact supplier cash billing is delayed, mark that explicitly and reconcile later.
- Render and exercise 390px mobile and desktop, EN/RU, light/dark, keyboard focus, long names, expanded details, low/exhausted states and failed estimates. Use actual screens, not just component tests.

Feedback session: ask the friend to choose a plan without coaching, explain what 5x means and what it is relative to, predict which model/action will use more allowance, perform a short task and a difficult task, then locate remaining usage/reset and recover from a seeded low-balance state. Record confusion, perceived capacity, estimate error and confidence. Test on a separately identified beta balance so artificial exhaustion/refills can be distinguished from normal usage.

## Definition of done and deferred scope

Tomorrow's intended deliverable is a deployed private beta with real accounting, clear plan comparison, honest model capabilities and recorded friend feedback setup. A static mockup or a percentage calculated from old reply counts does not satisfy it. No production pricing migration, real new-plan checkout, automatic top-up, annual pricing, referral discounts, arbitrary 40x marketing claim, or Work changes are needed for that feedback milestone.

Full public rollout follows evidence of useful capacity, resolved provider/tool failure behavior, verified fees and fair-use policy, private-tier capacity mapping, a coordinated account cutover and new checkout quote/discount behavior. The standalone adapter research is input to that work, not its acceptance.

## Reference examples, checked 18 September

- Codex describes Pro tiers as 5x/20x more usage relative to Plus and identifies message ranges as estimates: https://learn.chatgpt.com/docs/pricing
- Claude describes Max as 5x/20x usage relative to Pro with explicit time windows: https://claude.com/pricing

Borrow the named baseline and capacity comparison; keep our own reset period and funded grant explicit.
