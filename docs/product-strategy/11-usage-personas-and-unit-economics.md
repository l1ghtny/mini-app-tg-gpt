# Usage personas and unit economics

Last updated: 2026-07-27

> **Correction, 2026-07-27:** the first version of this analysis materially overstated OpenAI cost by pricing every input token at the uncached rate. The OpenAI dashboard shows $11.84 of total project spend for the same 30-day period, so the earlier $23.20 estimate for `lloaThfull` alone was impossible. All earlier pricing-change recommendations based on that estimate are withdrawn.

## Decision summary

Keep the public promise simple: customers buy monthly request allowances, not tokens, credits, or ruble-priced generations. Token and provider-cost accounting should remain an internal operating mechanism.

The available evidence now suggests the current tiers are more financially viable than the first reconstruction indicated. OpenAI prompt caching and chained Responses substantially reduce repeated-context cost. Do not change public prices or request allowances from this sample.

Before using per-user cost for future pricing decisions:

1. repair provider-cost telemetry;
2. add per-response cost guardrails that do not complicate the customer promise;
3. reconcile daily internal totals with provider billing exports;
4. run a 30-day paid-cohort pilot.

## Data and privacy boundary

This review used read-only production aggregates as of 2026-07-27. It inspected request ledger rows, model names, token counters, tool-call counters, image-quality energy, dates, conversation counts, subscription tier names, and generation status. It did not inspect prompts, assistant answers, document contents, email addresses, Telegram IDs, or payment details.

The named accounts were selected by the product owner as friend-tier test users. `F0rvarD` was added as a low-frequency comparison. An old anonymous friend account was used only as an anonymized burst-pattern example.

## Important cost-method caveats

Production `TokenUsage.total_cost` cannot currently be used as the source of truth: only 121 of 7,045 usage rows had a non-zero recorded cost at the time of review. Active pricing rows are missing for some models, OpenAI web-search and image cost are incomplete, and the schema does not store cached-input or cache-write tokens.

The original reconstruction multiplied every stored input token by the full input price. That is invalid for this workload. The backend uses stored Responses and `previous_response_id` chaining, while OpenAI charges cached input for current GPT models at one-tenth of normal input price. The OpenAI Usage API exposes `input_cached_tokens`, but the application currently discards that field.

The supplied OpenAI dashboard is the financial source of truth for the reviewed window:

- $11.84 total OpenAI project spend over the last 30 days;
- $8.22 July spend at the screenshot time;
- 15,051,563 total tokens;
- 1,449 total API requests.

A fresh database aggregate was close in token scale but cannot be converted reliably to dollars without the cached-token split. `lloaThfull` accounted for about 3.7 million stored OpenAI tokens in the current database window, but their exact dollar cost cannot be isolated from the dashboard. It is necessarily below the entire project's $11.84 and is likely in the single-digit-dollar range, not above $20.

## Observed consumer personas

| Persona | Production example | Observed behavior | Commercial interpretation |
|---|---|---|---|
| Occasional convenience user | `F0rvarD` | 44 requests across 8 active days in 180 days; one recent eight-request session; mostly Fast/Balanced plus a few images | Very profitable on Basic if retention is good; likely needs activation and habit-building more than more quota |
| Steady utility regular | `flow_of_spirits` | 229 requests in 180 days across 74 active days; median 2 requests and average 3.1 requests per active day; almost entirely Fast | Ideal Basic user; low provider cost, recurring usefulness, and little quota pressure |
| Episodic research/burst user | `ARONZ96` | 249 requests in 180 days across 40 active days; median 2 but maximum 59 requests in one day; Balanced/Smart-heavy with substantial web search | Advanced-shaped and likely more cost-variable than Fast-first use; exact margin needs cached-token attribution |
| Daily flagship creator/reasoner | `lloaThfull` | 437 consumed requests, including 76 images; active on 77 of 180 days; median 4 and maximum 31 requests per active day; strong Flagship and high-quality-image preference | Best observed Premium engagement case; exact provider cost requires cached-token attribution |
| Short-lived binge evaluator | anonymized friend account | 203 consumed requests in five days, then no recent use | Evidence that web acquisition can create burst shapes absent from the regular friend cohort; onboarding and anti-abuse controls must handle this pattern |

The sample does not contain a sustained web power user who works for several hours every day. Model that separately rather than treating the heaviest friend as the ceiling.

## Activity and cost evidence

The behavioral measurements remain valid: request counts, active days, model mix, images, context size, and search-call frequency came directly from the request and usage ledgers. The dollar figures in the original version do not remain valid.

The defensible financial conclusions are:

- the whole OpenAI project cost $11.84 in the dashboard window;
- `lloaThfull` cannot have cost more than the whole project and therefore did not cost $20+;
- Fast-first users are cheaper than Flagship-first users, but exact per-user cost is not currently measurable;
- OpenAI caching makes context squashing and response chaining economically more effective than the original analysis credited;
- Google, Perplexity, infrastructure, payment fees, and tax must still be added for complete contribution margin.

## What context squashing is accomplishing

Context squashing is materially useful. Across the last 90 days, median input size was approximately:

- `flow_of_spirits`: 5.4k tokens;
- `ARONZ96`: 16.8k tokens;
- `lloaThfull`: 12.2k tokens.

The 90th percentiles were approximately 13.5k, 78.3k, and 60.8k tokens respectively. Maximum observed inputs reached 144-151k tokens for the two heavy users, so compaction does not make every request cheap.

For expensive personas, cost is spread across cached and uncached input, cache writes, reasoning/output, tools, and images. Squashing and chaining control a major component, but exact savings must be measured rather than inferred from total input tokens.

## Current public-tier exposure

Production public tiers at review time:

| Tier | Price | Fast | Smart | Balanced | Flagship | Sonar | Sonar Pro | Daily image energy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Basic | 490 RUB | Unlimited | 300 | 100 | 15 | 300 | 0 | 100 |
| Advanced | 1,490 RUB | Unlimited | Unlimited | 250 | 25 | Unlimited | 0 | 300 |
| Premium | 2,490 RUB | Unlimited | Unlimited | 1,000 | 100 | Unlimited | 100 | 600 |

The risk is not that every subscriber will use every allowance. The risk is that the existing request caps are only weakly correlated with provider cost:

- one request can contain a short cheap answer or a long reasoning answer;
- one answer can trigger multiple web-search calls;
- a high-context Balanced request can cost more than many Fast requests;
- image energy controls image mix better than a raw image count, but its production catalogue and cost telemetry need repair;
- “unlimited” cheap-model traffic still needs documented technical fair-use and anti-automation protection.

The current schema cannot produce defensible per-model average dollar costs because it loses cached-input attribution. Request counts remain useful customer allowances, while provider billing totals and corrected usage telemetry must determine whether any pool is financially exposed.

## Recommended customer contract

Use language equivalent to:

> No token or credit counting. Each completed answer uses one request from the selected model group. You always see how many requests remain and when they renew.

Keep these concepts visible:

- Fast, Smart, Balanced, and Flagship request pools;
- requests remaining and renewal date;
- a plain-language image estimate by quality;
- a separate allowance for research-intensive or deep-search answers if introduced.

Keep these concepts internal:

- tokens;
- cached-token discounts;
- provider USD cost;
- routing thresholds;
- contribution-margin alerts.

## Recommended controls before repricing

### P0: repair cost observability

- Record input, cached input, output, reasoning, tool calls, image model/quality, and exact provider-reported cost where available.
- Add complete active pricing rows for every production model and provider.
- Price OpenAI web search and file search correctly.
- Persist image-generation cost instead of leaving it at zero.
- Reconcile daily internal estimates against OpenAI, Google, and Perplexity billing exports.
- Alert on per-request, per-user rolling-30-day, and tier-level contribution cost.

### P0: cap expensive work without exposing tokens

- Set sensible output and reasoning ceilings per model group.
- Limit ordinary web-enabled answers to a small number of search calls; route genuinely research-intensive work to a separate Deep Research allowance.
- Preserve automatic context squashing and add a hard maximum context policy for pathological conversations.
- Retain image energy, but align every energy value with current provider cost and remove stale quality rows.
- Apply automation/rate limits to unlimited pools while keeping normal human usage frictionless.

### P1: routing and packaging

- Default Basic to Fast or Smart and explain when Balanced/Flagship is worth using.
- Suggest a cheaper model when a task does not benefit from Flagship.
- Let exhausted users buy a simple pack of additional requests for one model group; do not introduce a universal token wallet.
- Consider a Power tier for sustained Flagship use instead of making Premium absorb every power-user shape.

## Pricing decision options

Do not silently reduce current friend access. Use their accounts as ongoing test cohorts.

Keep Basic at 490 RUB, Advanced at 1,490 RUB, Premium at 2,490 RUB, and the current public request allowances during the measurement period. The evidence does not support reducing Premium Flagship requests or adding a higher-priced Power tier today.

Request packs remain a useful product expansion for occasional bursts, but they are a convenience and conversion feature—not a response to demonstrated Premium losses.

Revisit prices or quotas only after a complete billing cycle with corrected cached-token telemetry and provider-cost reconciliation shows actual contribution margin by tier.

## Paid-cohort experiment

Run a 30-day cohort with at least 30 genuinely paying users and report:

- active days, requests per active day, and model-group mix;
- p50, p90, p95, and maximum provider cost per completed answer;
- search calls per answer and image cost by quality;
- provider cost per subscriber and per tier;
- subscription revenue after payment fees and tax;
- gross contribution margin by tier and at cohort level;
- percentage reaching 50%, 80%, and 100% of each allowance;
- conversion and retention differences between simple quota messaging and model guidance.

Do not finalize public power-user economics from averages. Price against p90-p95 behavior, then verify that the blended cohort still supports the desired margin.
