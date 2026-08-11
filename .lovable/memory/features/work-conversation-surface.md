---
name: Conversation-native Work surface
description: Work uses a normal persistent conversation while plans remain an internal execution detail.
type: feature
---

## Context

The visible plan, approval, and intent-button flow made general Work tasks feel
like a narrow form and left failed execution turns without a natural recovery
path.

## Decision

- The primary beta contract is a persistent user/assistant conversation.
- Starting or sending a message plans internally and begins execution directly.
- Show progress and bounded activity, not a mandatory plan approval screen.
- Keep source links, created files, previews, and prior results in the thread.
- Failed turns keep their request and context and expose retry plus a normal
  composer. Do not show raw provider or validation messages to users.
- Keep legacy plan endpoints during rollout; they are compatibility contracts,
  not the primary UX.

## Constraints

- Every start, message, and retry mutation needs an idempotency key.
- Do not weaken allowance reservation, accounting, artifact lineage, or worker
  durability when changing the surface.
- A rejected but substantial draft may be returned with advisory validation
  metadata. Empty and status-only drafts remain failed and retryable.
- Active-turn steering is not yet supported; do not imply that a new message is
  applied while the current worker turn is still running.
