# Document indexing feedback — 24 September 2026

Follow-up to the document-selection release. User authorized production first, then beta.

## Behavior

- Upload stays busy until the saved attachments have been refreshed, closing the gap between upload completion and readiness detection.
- Attached documents continue polling every three seconds while indexing, even after the picker closes. New chats use the selected IDs as their query key and treat unknown readiness as pending.
- The paperclip shows a spinner with the attachment count, plus a short upload/indexing status. Send is disabled until indexing finishes; the draft stays editable. Click, Enter, and late voice auto-send all respect readiness. Completion enables Send without automatically sending the draft.
- Auto tool selection is preserved. No backend API or model-routing changes are required.

## Validation

- Production frontend: 375 tests pass, build and focused ESLint pass.
- Beta integration: 408 tests pass and build passes.
- Chrome at 390 and 1440 px: home and existing-chat uploads block Send, retain editable drafts and count, retry a deliberately failed readiness request, keep polling with picker closed, and enable Send when ready. No real API mutations or model calls in these browser checks.
- First-try attachment selection/deselection regression rechecked separately.
- Existing full TypeScript error remains in unchanged `LazySyntaxHighlighter.tsx:30`; build passes.
- Notice migration: four tests cover localization, idempotency, preserving publication time, unrelated-row safety and downgrade. Single Alembic head, offline SQL, Ruff and whitespace checks pass.

## Announcement gate

What's New: required. Migration `xw0e1f2a3b51` extends the existing `2026-09-24-document-selection` announcement in English and natural Russian, retaining publication/read state. Push only after the frontend is verified in production. Production is the sole schema writer; beta carries the same history and verifies it.

## Release evidence

Frontend production commit: `61297c8`. Production flow [9236 / #81](https://teamcity.kosh.games/build/9236) passed all four jobs. Frontend image 81 digest `sha256:cfea7495533ce90104d7d8fce07a625916571713ffb584f7907155d36527b60b`; backend reused verified image 79. Public production browser acceptance passed all four cases. Rollout Healthy with two updated replicas. Announcement and beta rollout pending.
