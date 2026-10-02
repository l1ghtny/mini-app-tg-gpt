# Generation budgets and allowance policy v2

Owner-authorized backend implementation; activation is a separate release.
`SHARED_ALLOWANCE_GENERATION_V2_ENABLED` defaults to `false`. The additive
schema migration alone does not change grants or execution. Legacy requests
have no saved plan and keep the legacy behavior. This PR does not enable Claude,
change the production/beta eligibility gates, deploy, or publish an announcement.

## Execution and task selection

The server persists the execution plan at reservation. Quotes and execution use
the same version, effort, output cap and allowed tools. A stale signed quote is
rejected before provider work. Omitted effort selects the model's default;
explicit effort is preserved. A short prompt gets normal capacity. There is no
paid classifier or hidden semantic task label.

| Model | Normal cap | Expanded cap | Automatic effort |
| --- | ---: | ---: | --- |
| Luna | 8,000 | 16,000 | low |
| Terra | 12,000 | 24,000 | low |
| Sonnet 5 | 16,000 | 32,000 | medium |
| Sol | 16,000 | 32,000 | low |
| Opus 5 | 24,000 | 40,000 | medium |
| Astra | 24,000 | 48,000 | low |
| Fable 5.1 | 32,000 | 48,000 | medium |

The additive send/estimate field `response_length: "auto" | "long"` defaults
to auto. Long selects expanded output without increasing effort. An explicit
quantity of at least 1,000 words or three pages (English/Russian) also expands
capacity; this is a narrow convenience rule, not task classification. Explicit
medium or deeper effort selects expanded capacity. High has a floor of 32k
for models whose normal cap is at most 16k, otherwise 48k; xhigh/max have a 64k
floor. None is rejected for Astra/Fable. The existing provider model IDs and
rates remain unchanged; live acceptance must confirm all exposed effort/model
combinations, especially xhigh/max, before the UI offers them.

Output caps include private reasoning and visible answer tokens. They do not
reserve a guaranteed visible-answer allocation. Lower automatic effort,
sufficient capacity and one funded recovery address exhaustion; a token cap
cannot guarantee task quality or a complete answer.

Native Claude task targets are Opus 20k/32k/40k and Fable 24k/40k/48k for
normal/expanded/explicit tool work. High uses at least 48k, xhigh/max at least
64k. Input above 8k increases the target. Sonnet and GPT receive no task-budget
field. Supported Claude requests send `output_config.task_budget` and the
`task-budgets-2026-03-13` beta header. Task targets are soft guidance across
context and generation; output caps are hard per-response limits. Replaying
full history keeps the same native total, rather than subtracting tokens and
counting history twice. Signed completed Claude thinking/tool blocks, system
and tool schemas are retained across follow-ups. Incomplete private reasoning
is never fabricated or replayed in recovery.

Provider references: [Claude task budgets](https://platform.claude.com/docs/en/build-with-claude/task-budgets),
[OpenAI reasoning costs](https://developers.openai.com/api/docs/guides/reasoning#controlling-costs).
Treat native beta support as an activation acceptance check.

## Funding the workflow

Admission requires the entire selected profile to fit the available customer
allowance and explicit spend limit. It refuses insufficient capacity before
any provider attempt instead of shrinking output to 256 tokens. A hold is the
worst planned capacity, not a charge; settlement still charges successful known
usage once. Requests at or above 5% of the grant require existing spending consent.

Optional tools can answer directly, so their first call has the full output cap.
An explicitly required tool gets an 8k routing cap (16k at high or deeper).
Tool workflows fund one planning round, up to two tool calls, then a full
answer-only response. This deliberately replaces the legacy three-round loop
for v2. Exact duplicate tool calls reuse the result. The final answer and tool
funds are protected before routing. Additional rounds are not silently purchased.
Tasks requiring sequential new research need a new user request; benchmark this
tradeoff with real document/web tasks before enabling the policy.

Quotes include actual tool schemas, final-answer input headroom, decoded-image
reference bounds and all summary batches plus each summary's bounded retry.
They also include entitlement-dependent image-quota notices; send admission
receives the handler's final prompt so the strict capacity check prices the
instructions that execution uses. Luna-only requests with an exhausted shared
balance can still use their remaining Luna fair-use allowance.

Image-tool queries allow 8,000 characters, so quotes fund up to 32,000 UTF-8 bytes
per image call, including multibyte text. Schema validation, tool execution and
quote pricing share this limit. This increases image holds to fund the permitted
prompt size; settlement charges only returned usage. A detailed generated prompt
must not fail image admission after a paid routing call solely because the quote
assumed a 1,000-byte prompt.
Every provider step rechecks actual assembled context against the remaining
funds. Quote input counts use a local tokenizer with margin, not the provider's
native counter. Thus a hold remains a conservative estimate, not a proven exact
invoice ceiling. Cache hits are not assumed; tool/provider billing variance and
currency movement still need invoice reconciliation.

Known output exhaustion may receive one larger answer-only recovery. The
platform funds it separately from the customer reservation. Completed evidence
and visible partial text are carried forward; tools cannot execute again.
Truncated tool JSON, required first-tool calls, unknown usage, disconnects,
timeouts and other errors do not trigger recovery. A second exhaustion fails
without a third call. Terminal failures preserve residual partial text if the
assistant message still exists, and refund the customer. Successful recovery
charges successful usage within the original approved ceiling; supplier usage
records every known attempt, including refunded and recovery work. Unknown
usage stays conservative exposure until reconciled; it never becomes free money.
Provider completion without visible text or a tool call is a v2 failure, not a
customer charge. Its known supplier usage is retained; it does not trigger a retry.

## Allowances and policy versions

| Offer | Shared units | Luna/background units | Price |
| --- | ---: | ---: | ---: |
| Trial | 750,000 | 100,000 | free |
| Start | 1,750,000 | 290,000 | ₽490 |
| Plus | 3,500,000 | 500,000 | ₽990 |
| Premium | 8,750,000 | 1,000,000 | ₽2,490 |
| Max | 35,000,000 | 3,330,000 | ₽9,990 |

These are internal micro-USD accounting units, not withdrawable cash or a promise
of a number of replies. Trial has access to models but cannot fund every full
flagship profile; insufficient-capacity requests are rejected before spend.
Paid-equivalent base capacity rises 40%; trial rises from 500k to 750k. Private
tier mappings and public checkout restrictions are unchanged.

Grant policy `2026-10-02-v2` is distinct from rate version `2026-09-18-v1`.
Existing period accounts receive an idempotent policy adjustment, preserving
spent usage, holds, expiry and the trial lifetime clock. A flag rollback does
not reclaim the granted uplift. Future periods and their displayed catalog
retain the migrated policy. Explicit plan downgrade keeps the existing behavior
of preserving already spent/reserved amounts; it does not reset usage.

The earlier full-redemption scenario modeled FX120, payment/tax/refund costs,
fixed overhead and a 20% contingency; contribution estimates of roughly
17.5–26.6% were scenarios, not measured margins. This code does not implement
tax/refund forecasts or automatically claim margin from accounting units.

## Supplier envelope and loss pool

Keep `SHARED_ALLOWANCE_PROVIDER_BUDGET_UNITS` as an explicitly funded monthly
supplier envelope. All attempts, including Luna and known failed work, count
toward it; unknown attempts count their reserved budgets. PostgreSQL serializes
admission across users. The default 25M-unit beta envelope is not a scalable
production revenue policy and must be sized for the activation cohort.

`SHARED_ALLOWANCE_RECOVERY_BUDGET_PERCENT=20` allocates a separate exposure/loss
pool within that envelope. V2 admission counts failed attempts, pending/active
requests and recovery attempts against it. Active work reserves potential loss
before spend and frees that exposure after ordinary success. Recovery usage is
conservatively retained in this pool even if some successful usage is charged.
The pool therefore bounds both losses and concurrent work; its name does not
mean unlimited retries. Exhaustion returns `provider_failure_spend_paused`
before another billable attempt; the total envelope retains `beta_spend_paused`.

Size the envelope using verified paid subscriber workload, included Luna work,
trials/private cohorts, and explicit loss/concurrency capacity. Do not multiply
all subscription rows or allowance grants into a supposed cash balance: private
and free access are not payment receipts, and public checkout is still disabled.
Automatic receipt-based envelope scaling is deferred until a verified revenue
source exists. For now, explicitly update the configured funded envelope under
the existing operations process as the cohort grows, and reconcile provider
invoices. A successful migration/test run does not authorize a funding increase.

## Rollout, rollback and evidence

1. Review the PR and apply migration `xw0e1f2a3b62` through backend master,
   the shared database's sole migration writer. Carry identical history to beta;
   beta checks the schema rather than creating a second writer.
2. Keep the flag off while checking legacy sends, estimates and idempotency.
   Size the funded supplier envelope and loss pool for the pilot explicitly.
3. Run paid acceptance on enabled providers: ordinary/long output, explicit
   reasoning, long-history summaries, documents, web evidence, image work,
   reasoning-only exhaustion and one recovery. Measure first-answer latency,
   completed-task rate, known/unknown supplier cost, refunds and net contribution.
   Do not restore the separate Anthropic pause as part of this PR.
4. Verify authenticated mobile/desktop quote/confirm/send/cancel and SSE
   reconnect/reload. Check stale quotes, low balances, private upgrades, trial
   expiry, operational pause and duplicate sends. The local suite is not UI proof.
5. Enable the flag only for the existing authorized shared-allowance scope.
   On rollback, disable it for new admission. Already persisted v2 plans continue
   with their original caps independently of the flag; grants remain sticky.
   Do not downgrade accounting columns after v2 usage exists.

Local validation uses disposable PostgreSQL with synthetic provider responses;
no provider spend or deployment is performed. Migration tests cover upgrade,
downgrade and legacy balances; accounting tests cover concurrent recovery,
unknown usage, loss gating, grant upgrades and exactly-once charging. Provider
tests inspect native budgets, caps, stable prefixes, final tool disabling and
truncated-JSON accounting. Partial failure tests check deletion safety.
Executed checks after private-review fixes: 270 allowance tests and 26 focused
cancellation, availability, reasoning and provider-schema tests passed;
changed-file Ruff and whitespace checks passed. New integration regressions use
the real quote, send handler, reservation, provider capacity, image admission and
settlement paths with synthetic provider streams: zero-paid Luna, a 5,100-character
Russian image query, and the maximum four-byte UTF-8 image query bound.

## Frontend handoff

Current frontend main's `src/lib/api.ts`, `src/lib/allowance.ts` and
`src/components/AllowancePlans.tsx` were read from GitHub without editing the
dirty frontend checkout. Existing types ignore additive quote fields. The
confirmation dialog permits halving only above `minimum_ceiling_units`, so v2's
full-profile minimum prevents accidentally buying an unusable smaller cap.

- `src/lib/api.ts` and `src/types/index.ts`: optionally send `response_length`
  and explicit effort; keep the same payload in quote and send. Preserve
  preparation before timeline rewrite and re-quote the truncated history.
- `src/lib/allowance.ts`: add typed profile, cap, effort, native target and grant
  policy fields if displayed. Do not infer them from prompt length or prices.
- `src/components/AllowancePlans.tsx`: distinguish planned maximum capacity
  from an expected charge (v2 returns a 0-to-capacity range), and offer changing
  model/tools/length when a full profile cannot fit instead of halving its cap.
- Settings surfaces: expose only provider-accepted efforts; long writing must
  not silently increase reasoning. Refresh allowance examples for the new grants.
- `src/lib/sseParser.ts` and `src/lib/streamRecovery.ts`: acceptance coverage for
  partial answer plus continuation, terminal failure refund, cancel and reload.
  No SSE event schema changes are required by this PR.

These are concrete follow-ups and acceptance gates, not same-session frontend
implementation. Existing send/quote contracts remain compatible.

## Release gate and held announcement draft

What's New: required when activated, because allowances and recovery are visible.
No draft or inactive row is inserted into the shared feed. After production
acceptance, prepare one scoped idempotent EN/RU notice migration, checking prior
long-response notices to avoid duplicate claims. Backend version: unchanged
in this gated PR; next activation requires a minor bump from then-live production.
Last recorded deployed baseline was 2.0.2, so 2.1.0 is provisional and must be
rechecked alongside other pending release scope. Frontend version: unchanged
here; separately released UI work gets its own version decision on the shared
major. Verify API/bot/frontend Sentry release IDs after the approved deployment.

Held English draft, to revise against verified production scope:

**More AI allowance and room for longer answers**

Paid plans now include 40% more shared AI allowance, and the free trial includes
more allowance too. Long responses have more room to finish. If an answer reaches
its response limit, Lightny can try once to continue it automatically. Unsuccessful
tasks do not consume your allowance. Your current balance is shown in Settings.

Held Russian draft:

**Больше лимита ИИ и места для длинных ответов**

В платных тарифах общий лимит ИИ увеличился на 40%. Пробный доступ тоже получил
больше лимита. Длинным ответам теперь выделяется больше места. Если ответ
достигает ограничения по длине, Lightny может один раз попробовать продолжить
его автоматически. Неудачные запросы не расходуют ваш лимит. Остаток можно
посмотреть в настройках.
