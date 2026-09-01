---
name: OpenAI commentary activity
description: Phase-marked OpenAI commentary becomes bounded public chat progress, never answer text or reasoning.
type: tech
---

## Context

OpenAI GPT-5.4 and newer also expose assistant message `phase`. A
`phase=commentary` message is intentionally user-visible progress, while
`phase=final_answer` is answer content. This contract is separate from model
reasoning and from concrete tool activity such as web searches and opened URLs.

## Decision

- For phase-capable OpenAI models, prompt for short commentary only on multi-step,
  tool-heavy, or meaningfully analytical requests. Route commentary into the
  transient chat `turn` activity and never append it to the answer body.
- Track message phase by item ID and output index because text deltas do not always
  repeat the phase.
- Whitespace-normalize and length-bound the completed commentary before publishing
  it as a public activity label.
- Keep concrete search, page, file, and source events in the detailed timeline and
  preserve source URLs after completion.
- Keep commentary OpenAI-specific unless another provider offers an equivalent
  explicitly user-visible message phase.

## Gotchas

- Commentary labels are model-authored public copy, so bound and whitespace-normalize
  them before persistence. Hide the turn label after completion while retaining
  concrete tool and source history.
- During streaming, prefer the latest commentary label as the collapsed headline and
  keep concrete tools in the expanded timeline. Reconnect/retry states are the only
  higher-priority headline because they explain an actual wait or transport problem.
- Commentary is optional; simple requests and some valid model responses may emit none.
- Never reinterpret reasoning summaries or raw reasoning text as commentary.
