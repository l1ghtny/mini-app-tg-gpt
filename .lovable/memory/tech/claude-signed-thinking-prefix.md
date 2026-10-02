---
name: Claude signed thinking prefix
description: Keep Claude system prompts and tool definitions stable while returning tool results.
type: tech
---

Claude Fable 5.1 binds a returned thinking block to the system prompt, tool list,
and preceding messages. A tool follow-up that changes the system prompt or
advertised tools fails with HTTP 400 (`Invalid signature in thinking block`).

For one Claude tool-use cycle, append the original assistant blocks and matching
tool results to history without rewriting earlier messages. Keep the system
prompt and advertised tool definitions unchanged until the assistant finishes.
Enforce the app's tool-call limit when executing calls, while still advertising
the same definitions in the provider request. OpenAI turn behavior can still
remove tools and add completion instructions between calls.

Provider reference: https://platform.claude.com/docs/en/api/errors#thinking-block-no-longer-matches-the-conversation
