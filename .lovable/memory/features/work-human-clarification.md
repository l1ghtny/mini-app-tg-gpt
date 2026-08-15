---
name: Durable Work clarification
description: Persist and resume model-initiated questions without turning Work into a rigid form flow.
type: feature
---

# Context

Some Work tasks have one missing fact that materially changes the evidence,
cost, or safe outcome. Treating every ambiguity as an error makes the agent
brittle; guessing in consequential cases makes it unreliable.

# Decision

- Expose one strict provider-neutral `ask_user` function to the executor.
- Ask only for material ambiguity; otherwise proceed with a labelled assumption.
- Persist the question, reason, provider response ID, and call ID.
- Pause the run as `waiting_for_user`, release worker/provider resources, and
  terminate the current SSE stream.
- Accept the answer idempotently, requeue the same run, and continue through the
  original provider `previous_response_id` and `function_call_output.call_id`.
- Allow one pending question and at most two clarification rounds per run.
- Never request credentials, tokens, payment data, or other secrets. Do not put
  question or answer text in metrics or logs.

# Integration rules

- Waiting runs still count against the user's active-run limit.
- The normal Work composer must not accept another turn while the structured
  question is pending.
- Render the question inline in chronological conversation history, not as a
  modal or separate workflow screen.
- Cancellation marks pending questions cancelled and retains normal allowance
  refund semantics.
- Keep the API response field optional on the frontend during rolling deploys.

# Gotchas

- Production and beta share PostgreSQL. Apply the additive schema migration in
  production before deploying the beta runtime that reads the new table.
- Do not keep an SSE connection or worker lease open while waiting for a person.
- Do not create a new Work run for the answer; resume the original durable run.
