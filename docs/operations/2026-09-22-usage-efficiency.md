# Usage efficiency repair — 2026-09-22

Status: implemented and tested in isolated backend/frontend worktrees on `codex/usage-efficiency-20260922`. Not committed, pushed or deployed. No customer balances, allowance sizes, subscription prices, primary models or reasoning effort defaults changed.

## Cause and repair

The affected beta account's seven Sol requests used 33.44% of the Smooth/Premium allowance. The expensive conversation repeatedly replayed historical images, reaching about 169,000 input tokens. A cached request was much cheaper. Expected-cost UI estimates omitted this image work while conservative admission reserved about 55%.

- Historical image pixels are replaced by stable references and previous observations. Current uploads remain visible at explicit high detail. Originals remain stored and ownership-checked; a bounded internal tool rereads only selected images through Luna when missing visual facts are needed. The user's selected model produces the answer. Up to two images per inspection and two tool calls per request remain allowed.
- History compacts by tokens, not characters. Incremental summaries preserve facts and image references and stay stable until the next threshold, avoiding repeated summaries and improving prefix reuse. Legacy summaries retain addressable original images.
- Quote and runtime use the same prepared context, current prompt/images and instructions. Token estimates include a margin and image detail costs. Optional tools no longer reserve a second complete answer. Ordinary unconfirmed requests are capped at 5%; genuinely expensive requests and costly image generation still require confirmation.
- One-shot helper calls avoid cache-write premiums; Claude now caches growing history as well as the system prompt. Selected models and reasoning defaults are unchanged.
- Dialog shows expected usage and maximum charge, with a specific lower-limit action. Russian/English copy and mobile layout reviewed. Existing API shape and SSE contract remain compatible.
- Tokenizer data is warmed into the Docker dependency layer; runtime requests do not need to download it. Added context-count and over-budget-attempt logs without message bodies.

## Evidence

Read-only simulation against the affected live conversation: 90 messages and 19 image occurrences become 33 prepared messages and zero old image pixels on a text-only follow-up. Eighteen distinct original images remain addressable. The simulated quote is 0.86–1.16% expected / 2.73% ceiling of Premium, including a bounded placeholder for the required summary. This is a new follow-up estimate, not a replay or refund calculation.

Real provider acceptance used synthetic labels/history, no customer content and no production-account writes:

| Scenario | Shared allowance units | Premium equivalent | Start equivalent |
| --- | ---: | ---: | ---: |
| Seven correct Sol follow-ups after 19 historical images, with cache reuse | 15,191 | 0.2431% | 1.2153% |
| Routine Sol follow-up, first uncached request | 7,982 | 0.1277% | 0.6386% |
| New image read and calculation | 18,932 | 0.3029% | 1.5146% |
| Targeted reread for a previously unrecorded batch code | 10,896 | 0.1743% | 0.8717% |
| Long-history answer after compaction | 25,652 | 0.4104% | 2.0522% |
| Two Claude follow-ups with history caching | 6,527 | 0.1044% | 0.5222% |

Long-history compaction additionally used 1,300 units from the separate Luna allowance. It preserved exact budget, deadline and dietary constraints. The second Claude request reused 2,242 tokens and used 748 units versus 5,779 on the first request. Pricing the seven Sol requests' recorded cached tokens as cache writes gives a modeled 0.97% Premium equivalent; that is a counterfactual, not another measured run. None of these figures guarantees the cost of seven arbitrary tasks, large new attachments, image generation, or long reasoning outputs.

## Validation

- Full allowance suite: 76 passed before final token-window/cache refinements; final affected context/estimate/provider/image suite: 35 passed after them. Tests use isolated schemas on the test database, never root pytest fixtures which reset the public schema.
- Focused Ruff and `git diff --check` pass.
- Frontend: 10 tests, TypeScript, focused ESLint and production build pass. Existing build warnings only. Actual rendered dialog reviewed at 320px, 390px and 1280px; lower-limit/cancel behavior has component coverage.
- Real OpenAI and Claude streaming adapters used in synthetic acceptance. Owned-image selection, malformed reference rejection, cost reconciliation, summary persistence guards and per-attempt limits have regression coverage. Synthetic metering did not exercise deployed HTTP auth/send/resume or account settlement; those remain rollout checks.
- Affected backend production/beta source files and frontend dialog source match on the inspected remote-tracking refs, so the same patch applies to both. Recheck refs immediately before integration.

## Release handoff

Backend worktree: `/private/tmp/lightny-usage-efficiency/backend`.
Frontend worktree: `/private/tmp/lightny-usage-efficiency/frontend`.
Evidence and synthetic harnesses: `/private/tmp/lightny-usage-efficiency/` (local operational artifacts, not application dependencies).

Release backend and frontend to beta together first; no schema migration is required. Verify immutable deployed revision/image, authenticated send/stream/resume, old-image follow-up, targeted old-image inspection, new upload, image edit and the quoted/reserved/settled usage. Check context logs show zero historical pixels on text follow-ups and no systematic attempt-budget underruns. Then roll the identical scoped change to production. Preserve unrelated existing work and unrelated feature branches. Rollback is the prior backend/frontend images; no data backfill is required.

Frontend follow-up is already implemented in `src/components/AllowancePlans.tsx` and its tests. No API normalizer, types or SSE parser changes are required. Old frontend builds remain compatible but retain old warning wording until updated.

Limitations: summaries are lossy, so precise missing visual details are retrieved explicitly; arbitrary old image batches are bounded. Claude text budgeting uses the OpenAI tokenizer with a margin rather than a provider count endpoint and is monitored against actual attempts. Large new images, genuinely long responses and expensive tools can still cost materially more. This repair removes repeated work; it does not conceal real supplier cost or replenish past usage.

## Provider references

- [OpenAI image detail and token accounting](https://developers.openai.com/api/docs/guides/images-vision)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [Claude vision sizing](https://platform.claude.com/docs/en/build-with-claude/vision)
- [Claude growing-history prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
