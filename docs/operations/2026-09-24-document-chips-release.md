# Document attachment history and composer release

## Scope

User authorized implementation and release to production first, beta afterward.

- Removable filename chips before sending, with indexing indicators and the existing Send readiness guard.
- Non-interactive filename chips in sent user messages when the attachment selection changes.
- A compact “Files in this chat · N” control for an unchanged, accepted selection.
- Server-owned filename snapshots in the existing MessageContent.data JSON. Later detach, rename, or deletion does not rewrite sent history. Legacy messages are not backfilled with today's attachments.
- Auto tool choice and existing document search behavior remain intact.

What's New: required. Extend the existing `2026-09-24-document-selection` item after production feature verification; preserve publication/read state and avoid duplicate notices. No schema migration is needed for the attachment records.

## Validation before release

- Backend: 40 focused document snapshot, document selection/search/indexing and shared-context tests passed. Integration test uses a unique private test schema; verifies ownership filtering, immutable original filenames, known-empty metadata, model input exclusion and edit replacement.
- Frontend: 378 tests passed, then 10 focused tests passed including an added API normalization test. Production build and changed-file ESLint passed.
- Full TypeScript check retains the pre-existing LazySyntaxHighlighter.tsx:30 fallback `language` prop error; no new type errors.
- Browser: existing selection and indexing checks pass at 390 and 1440 px on home/chat. New history checks cover rejected send/removal, successful send, unchanged follow-up, detachment and reload with all APIs intercepted; no paid provider calls.

## Pending notice draft

EN: Selected filenames now appear above the message box. After sending, they stay with the message; unchanged follow-ups use a compact “Files in this chat” row. Removing a file later leaves its name in the original message.

RU: Названия выбранных файлов теперь видны над полем сообщения. После отправки они остаются в сообщении, а над полем ввода появляется компактная строка «Файлы в чате». Если убрать файл из чата, его название останется в исходном сообщении.

## Deployment

Pending production feature verification, notice publication, then beta release.
