# Generation budgets, no-hold charging and iterative research

Backend PR #20 and its linked frontend companion are one review scope. No merge,
activation, deployment, provider restoration or supplier-envelope increase is
authorized here. `SHARED_ALLOWANCE_GENERATION_V2_ENABLED=false` remains the default.
The flag name is historical: future admissions with it on now use `no-hold-v3`
accounting and `iterative-v3` execution. Saved earlier v2 plans keep held accounting.

## Customer charging and consent

Three separate values are persisted: customer-approved charge maxima (shared and
Luna), the normal supplier execution ceiling, and a separately bounded recovery
ceiling. New quotes and admissions consume no customer hold. Quotes create no
task slot or supplier attempt. Old quote fields remain compatible with existing
clients: `ceiling_units` is a customer charge maximum, and the minimum is an
admission floor rather than a full-profile supplier reservation.

Normal text, web and document requests need no spending dialog. Image operations
require a deliberate selected/required image action and signed consent; auto mode
does not grant image permission. An image discovered without permission receives
an explanation instead of execution. The customer can reduce an approved maximum.
Rich-account explicit caps below 10,000 units are rejected before supplier work;
near-zero remaining balances can approve their smaller remainder. Luna text can
approve zero shared units when fair-use capacity remains. Required paid tools at
zero shared balance fail before provider work; optional paid tools are omitted.

Signed five-minute estimates bind stable task inputs, history, system preferences,
model, effort, length, tools, document stores, image quality and approved maxima.
Small balance movements do not invalidate consent. Send rechecks current access
and balances atomically and can lower, never raise, the signed maxima. Edit and
regenerate preserve the frontend's consent-before-rewrite contract: re-quote the
rewritten history within the prior approved maximum. An account/history/task
change still requires a fresh signature.

Known successful usage settles exactly once under row locks against the original
account and its admission period, clamped to remaining capacity and the approved
maximum. No negative balances, future-period debt or account transfer occurs.
Platform support pays bounded excess. Failed/cancelled logical tasks retain the
refund policy, including successful child work. Unknown usage consumes no customer
hold or charge, but remains internal exposure until verified reconciliation.
New trial clocks start on a known successful result; legacy trial semantics remain
unchanged. Grant upgrades never refill prior spend, and grant policy stays sticky
through flag rollback.

## Ownership and concurrency

Two live logical tasks per user are admitted atomically in PostgreSQL across
conversations, workers, accounting scopes and periods. Summaries, tools and
recovery use the parent slot. Duplicate client IDs do not create another task:
the existing message/stream is reused once available; the admission race returns
409 while the initial result link is not yet recorded. Expensive image calls in
the shared pipeline use the same parent; upload/proxy endpoints do not generate
billable images.

Queued tasks have a five-minute lease. A worker claims a unique owner once;
each supplier/batch admission verifies ownership and renews the lease, bounded
by the original 900-second deadline. Provider work has a 240-second timeout.
An expired worker can record late invoice usage but cannot start another call or
charge a fenced task. New admission and stale cleanup close expired tasks while
retaining unknown spend. Explicit cancellation fences the ledger owner as well
as setting the existing stop flag. SSE disconnect/reconnect does neither. Two
saved no-hold tasks also keep the slot guard during flag rollback; ordinary legacy
operation otherwise retains its existing admission behavior.

## Adaptive research and answer capacity

Pilot defaults: six unique tool executions, six planning turns, one final-only
phase, two read-only operations in parallel and one image operation. Exact
duplicate calls reuse cached results without another attempt or operation; every
planning turn still counts. Refined sequential web and document searches are
allowed. Two identical/empty retrieval payloads stop further work with that tool.
Every emitted call receives a matched result, including a truthful blocked result.

If a web/visual research helper reaches a known output cap, its failed supplier
usage stays recorded and the helper is not retried. Return a matched evidence-gap
result, retain successful sibling/earlier evidence, block undispatched research,
and finish with an answer-only turn. Incomplete helper text is not treated as
verified evidence. Unknown usage, transport failures and cancellation retain
their existing failure/exposure behavior.

The 120-second research clock starts at the first admitted actual tool operation.
Time, operation, context and funding limits stop new research and allow a full
answer from available evidence. Instructions require citations and explicit gaps.
Early completion returns immediately. A final phase cannot restart research.
Tool results are bounded to 2,048 tokens, preserving complete citation URLs and
filenames. Full Claude history, signatures, system and tool schemas are retained;
the 192,000-token context safety bound stops new research before final input plus
its output cap would exceed that bound. Provider-specific live limits remain an
activation check; this is a conservative internal bound, not a model specification.

Context protection now includes serialized planning output and matching blocked
results before a new planning turn. Before dispatch, count every actual emitted
call and its matched result, including cached and skipped calls. Admit fixed-size
evidence only while assembled input plus the full final output cap fits. Stop
new research when it does not; never shrink the answer cap to make research fit.
Result bounds include JSON escaping in provider input. The signed Claude history
and stable schemas are retained; no model-based research compaction is added.

Future work approved in principle: protocol-safe research evidence compaction
(option 3), to support deeper investigations. It can incur additional inference;
trigger it only when useful, price and cap every pass within the existing task/
platform budgets, and measure whether it saves net cost or improves completion.
Do not add an unbounded summarization loop or a compulsory extra call per request.
Preserve source identifiers, evidence gaps and signed/tool protocol invariants.
Implementation and activation remain deferred. The detailed internal backlog is
`.lovable/memory/tech/research-evidence-compaction-backlog.md`.

Supplier steps are admitted incrementally. Parallel batches commit all admissions
before dispatch; a failed batch admission rolls back every step and counter.
Before planning or tools, protect a full final response priced from assembled
history plus bounded prospective output/evidence. This protection also counts
against other live tasks in the global/user exposure guard. Legacy supplier
admissions also honor saved live final protections during flag rollback. A turn that may answer
directly always gets the full normal cap. Only a forced initial tool-routing turn
can use the routing cap. If another research turn cannot be funded, use one final
call rather than a tiny answer or another tool attempt.

One answer-only recovery is allowed after known `max_tokens`/`max_output_tokens`
exhaustion. It retains retrieved evidence and public partial text, never replays
search/image side effects, and never retries unknown/ambiguous usage. Both funding
guards and context checks are re-evaluated. Recovery is conditional on funding;
the system cannot promise it after multiple full-cap expensive failures.

## Model profiles

| Model | Normal cap | Expanded cap | Default effort |
| --- | ---: | ---: | --- |
| Luna | 8,000 | 16,000 | low |
| Terra | 12,000 | 24,000 | low |
| Sonnet 5 | 16,000 | 32,000 | medium |
| Sol | 16,000 | 32,000 | low |
| Opus 5 | 24,000 | 40,000 | medium |
| Astra | 24,000 | 48,000 | low |
| Fable 5.1 | 32,000 | 48,000 | medium |

No paid classifier or semantic task label is used. Short prompts keep normal
capacity. `response_length=long`, explicit quantities of at least 1,000 words or
three pages, and explicit medium/deeper effort select expanded capacity. High
has a 32k/48k floor and xhigh/max a 64k floor. Explicit choices are preserved;
none remains unsupported for Astra/Fable. Live effort acceptance is required
before offering additional controls.

Caps include reasoning and visible output; they cannot guarantee an answer-token
allocation. Native Claude targets are Opus 20k/32k/40k and Fable 24k/40k/48k for
normal/expanded/forced-tool work, increased to at least 64k/80k respectively when
iterative tools are available. Full-history replay uses the same native total
without double-decrementing it. Sonnet/GPT get no native task-budget field.
Claude beta support and actual model quality need provider acceptance before use.
See [Claude task budgets](https://platform.claude.com/docs/en/build-with-claude/task-budgets)
and [OpenAI reasoning costs](https://developers.openai.com/api/docs/guides/reasoning#controlling-costs).

## Exact funding defaults and tradeoffs

Units are micro-USD at the existing rate snapshot, not customer currency. The
funded global supplier envelope stays 25,000,000 units ($25) per calendar month;
the existing 20% internal loss/exposure pool stays 5,000,000 units ($5). Neither
private grants nor subscriptions implicitly raise that funded envelope.

Normal task ceiling is the minimum of: $6; approved shared + Luna maxima + grace;
and a planned headroom estimate (at least $0.50, otherwise three full direct calls
plus $0.30 research and summary/image headroom). Recovery is separately priced and
capped at $6. Supplier budgets are capacities, not mandatory calls or invoices.
At most two tasks therefore have an individual-ceiling bound of $24 including
two recoveries; simultaneously admitted unresolved work plus protected finals is
also bounded by the smaller user cap and $5 global pool, and all actual supplier
work by the $25 envelope. Token/input bounds are conservative estimates; unexpected
invoice overruns are recorded and block further work, not erased.

The rolling 30-day user loss/exposure cap is `clamp(50% of grant, $2, $5)`.
Grace per task is `min($4, user cap)`. Unknown costs remain in exposure even after
their date window expires. Known net losses include failed children, recovery and
refunded parent tasks; proportional successful paid/fair-use credits prevent old
charges from hiding new-window losses. All unsettled supplier work is conservatively
treated as exposure. Each request saves the limits applied at admission.

| Plan | Shared grant | Luna capacity | Grace per task | User loss/exposure cap |
| --- | ---: | ---: | ---: | ---: |
| Trial | $0.75 | $0.10 | $2 | $2 |
| Start | $1.75 | $0.29 | $2 | $2 |
| Plus | $3.50 | $0.50 | $2 | $2 |
| Premium | $8.75 | $1 | $4 | $4.375 |
| Max | $35 | $3.33 | $4 | $5 |

The $2 floor is deliberately above 50% for small plans: $0.75 cannot admit a
normal 32k Fable answer, whose output capacity alone prices at $1.60. Default
single-answer admission is covered for every trial model. A hypothetical customer
using all paid and Luna capacity plus the entire loss cap gives supplier-cost
envelopes of $2.85/$4.04/$6/$14.125/$43.33 for Trial/Start/Plus/Premium/Max.
These are capacity scenarios, not measured costs or margin forecasts; the global
$25 guard can stop work sooner. Heavy parallel flagship research and full-cap
recovery can still be refused. Six research operations are a maximum, not a
guaranteed allowance to spend through six maximum-sized responses.

Do not activate without a funded pilot and measured invoice economics. Validate
task success, useful answer rate, refunded/unknown cost, full-cap stops, guard
rejections, retries and effective margin including payment/infra/support costs.
Raise funding or reduce exposed expensive profiles only through an explicit
product/funding decision; do not hide the constraint by shrinking answer caps.

## Rollout, reconciliation and release gate

Migration 63 is additive and follows 62 as the single head. Historical rows default
to held accounting; migration never changes balances or activates the flag.
Deploy schema first, then compatible readers/writers to every API/bot worker,
then the frontend companion, before any separately approved flag activation.
Old binaries cannot settle a no-hold row safely: after activation, rollback the
flag in compatible code, not the schema/binary until saved tasks are drained.
Future flag-off requests use legacy holds; saved v2 held plans and new no-hold
plans finish under their original policy. Increased grants remain sticky.

For unknown usage, investigate the persisted provider identity and supplier
invoice/response records. Only verified usage may finish/reconcile an attempt;
never set missing usage to zero just to reopen budgets. A failed parent stays
refunded even when its late invoice is recorded. Pending successful tasks settle
only after known usage, under the original account/period and approved cap.

What's New: required for coordinated activation (larger grants, longer answers,
ordinary no-hold conversations and iterative research). Keep one EN/RU draft in
documentation; do not insert inactive/beta rows in the shared feed. Check existing
notices and publish a scoped idempotent announcement migration only after verified
production availability. The frontend copy/error/spacing companion alone needs
no separate announcement. Deployed versions were read as backend 2.0.2 and frontend
`tg-mini-frontend@2.1.0+118`; proposed sources are backend 2.1.0 (compatible capability)
and frontend 2.1.1 (compatible integration fix). Recheck baselines and consolidate
with other pending releases before merge/deployment; verify API/bot/frontend Sentry
release IDs after an authorized rollout. Nothing is deployed by this PR.

Held English announcement: **More room for AI answers** — Your AI allowance now
has more capacity. Ordinary conversations no longer reserve a worst-case charge
before each reply. Research can follow up on earlier search results, and long
answers can continue once when funding permits. Settings shows actual usage.

Held Russian announcement: **Больше возможностей для ответов ИИ** — Лимит ИИ стал
больше. Обычные запросы больше не резервируют максимальный расход перед каждым
ответом. Поиск может уточнять результаты предыдущих запросов, а длинный ответ —
продолжиться ещё раз, если хватает ресурсов. Фактический расход виден в настройках.

Validation is recorded in the PR and task state. Synthetic provider tests prove
accounting and orchestration behavior, not paid provider quality, native beta
support or production rollout. Desktop/mobile in-app checks use the actual
consent component with synthetic quotes; authenticated live send/stream/resume,
Telegram cancellation, paid model quality and pilot economics remain activation gates.
