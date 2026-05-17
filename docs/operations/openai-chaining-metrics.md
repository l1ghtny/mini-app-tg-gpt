# OpenAI Chaining Metrics Contract

This document defines metric names and reason tags used for OpenAI response chaining.

## Event Metrics

- `openai.chain.attempted`
  - Emitted when a request is sent with `previous_response_id`.
- `openai.chain.succeeded`
  - Emitted when chained request creation succeeds.
- `openai.chain.not_used`
  - Emitted when chaining is enabled but skipped before OpenAI call.
- `openai.chain.fallback`
  - Emitted when chained path was attempted but runtime fell back.

## `openai.chain.not_used` Reasons

- `disabled`
- `no_response_id`
- `no_chain_timestamp`
- `no_context_fingerprint`
- `context_fingerprint_mismatch`
- `expired_inactivity_window`
- `missing_current_turn_payload`

## `openai.chain.fallback` Reasons

- `create_rejected_previous_response_id`
- `exception_retry_exhausted`

## Suggested Sentry Dashboards

1. Chaining adoption:
   - `openai.chain.attempted` / `message_sent` by model.
2. Chaining stability:
   - `openai.chain.succeeded` vs `openai.chain.fallback` by model.
3. Skip diagnostics:
   - `openai.chain.not_used` grouped by `reason`.
4. Fallback diagnostics:
   - `openai.chain.fallback` grouped by `reason`.

