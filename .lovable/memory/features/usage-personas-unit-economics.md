---
name: Usage personas and unit economics
description: Keep request-based customer quotas while using hidden provider-cost telemetry and guardrails to protect tier economics.
type: feature
---

# Context

A read-only production review on 2026-07-27 compared friend-tier behavior for `flow_of_spirits`, `ARONZ96`, `lloaThfull`, and `F0rvarD` without reading message contents.

# Decision

- Preserve the public “requests, not tokens or credits” contract.
- Treat Fast regulars, burst researchers, and daily Flagship/image users as different cost personas.
- Do not trust production `TokenUsage.total_cost` until pricing coverage, web-search cost, cached/cache-write tokens, and image cost are fixed.
- Add internal per-response and rolling-user cost controls without exposing tokens to customers.
- Validate public allowances on a 30-day cohort of at least 30 paying users before final repricing.

# Constraints and gotchas

- Context squashing reduces input cost but does not control long reasoning/output, repeated web-search calls, or image cost.
- The original $23.20 power-persona estimate was invalid because all input was priced as uncached; the whole OpenAI project actually spent $11.84 in the supplied 30-day dashboard window.
- Keep current public prices and quotas until exact cached-token telemetry and provider billing reconciliation show otherwise.
- A request count is not a reliable provider-cost ceiling unless output, tool calls, context, and image quality are also bounded internally.
- Full methodology and figures are in `docs/product-strategy/11-usage-personas-and-unit-economics.md`.
