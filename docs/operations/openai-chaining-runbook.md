# OpenAI Chaining Runbook

## Feature Flags

- `OPENAI_CHAINING_ENABLED` (`True`/`False`)
- `OPENAI_CHAIN_MAX_INACTIVITY_DAYS` (integer, default `14`)

## Safe Rollout Steps

1. Deploy with `OPENAI_CHAINING_ENABLED=False`.
2. Enable for canary environment/users.
3. Watch:
   - `openai.chain.attempted`
   - `openai.chain.succeeded`
   - `openai.chain.not_used` by reason
   - `openai.chain.fallback` by reason
4. Expand rollout if fallback ratio remains low and no output quality regression is reported.

## Emergency Rollback

Set `OPENAI_CHAINING_ENABLED=False` and redeploy.

## Debug SQL Snippets

Inspect chain fields for a conversation:

```sql
select
  id,
  model,
  last_openai_response_id,
  openai_chain_updated_at,
  openai_chain_context_fingerprint
from conversation
where id = :conversation_id;
```

Find conversations with stale chain pointers:

```sql
select
  id,
  openai_chain_updated_at
from conversation
where last_openai_response_id is not null
  and openai_chain_updated_at < now() - interval '14 days';
```

## Expected Behavior on Timeline Rewrite

When user edits/deletes/regenerates in middle of chat:
- conversation tail is truncated in DB
- OpenAI chain fields are cleared
- next request rebuilds context from DB and starts fresh chain

