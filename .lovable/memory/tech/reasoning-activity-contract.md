---
name: Provider-neutral reasoning activity
description: OpenAI and Google visible summaries share one bounded SSE and persistence contract.
type: tech
---

## Context

OpenAI and Google expose optional summaries of model reasoning through different
stream shapes. Google may include the first summary on `step.start`, and either
provider may omit summary text entirely. Provider-authored headings are not
stable product copy and must not become the collapsed UI label.

OpenAI GPT-5.4 and newer also expose assistant message `phase`. A
`phase=commentary` message is intentionally user-visible progress, while
`phase=final_answer` is answer content. This is a separate contract from reasoning
summaries and may drive the transient collapsed label.

## Decision

- Use `ReasoningActivity` for OpenAI and every Google interaction path.
- Preserve rolling-compatible `reasoning.summary.delta` and
  `reasoning.summary.done` events while adding provider, activity, and segment
  identifiers.
- Aggregate multiple provider summary segments in order and send the complete
  aggregate on every done event so the client can reconcile streamed detail.
- Persist the same bounded aggregate shown to the user, capped at 16,000
  characters.
- Emit lifecycle status for empty thought blocks, but never fabricate visible
  summary text.
- Treat summaries as provider-authored detail, not raw chain-of-thought or
  product-controlled status copy.
- For phase-capable OpenAI models, prompt for short commentary only on multi-step,
  tool-heavy, or meaningfully analytical requests. Route commentary into the
  transient chat `turn` activity and never append it to the answer body.
- Keep commentary OpenAI-specific unless another provider offers an equivalent
  explicitly user-visible message phase. Do not reinterpret thought summaries as
  commentary.

## Gotchas

- Google `step.start.step.summary` can contain text that never arrives as a
  later delta.
- New deltas after a completed segment must reactivate the frontend activity.
- Redis Stream field values do not accept Python booleans. Keep event metadata
  typed in service code, and rely on `RedisEventBus` to encode top-level booleans
  as `0` or `1` at the transport boundary.
- Keep stable localized labels in the frontend; expanded detail may contain the
  provider summary in the language requested by the user.
- Commentary labels are model-authored public copy, so bound and whitespace-normalize
  them before persistence. Hide the turn label after completion while retaining
  concrete tool and source history.
- During streaming, prefer the latest commentary label as the collapsed headline and
  keep concrete tools in the expanded timeline. Reconnect/retry states are the only
  higher-priority headline because they explain an actual wait or transport problem.
