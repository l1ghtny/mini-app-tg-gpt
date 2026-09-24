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

Feature release: backend `53f99ac`, frontend `0a3b616`; production flow [9266/#84](https://teamcity.kosh.games/build/9266) passed all four children. Both rollouts are Healthy with two updated replicas; runtime hashes match source and both public domains return JSON readiness. Backend84 digest `ac8c5675ed9b20aee31be3ff9b693062331c7b77545dbe5340f5ec7955b17df2`; frontend84 digest `a5f0a6bb5e5e5f6b6bda9f1ea37f417b30f0b30aa5d175e5a9c352833a0f1275`.

Real production acceptance on backend84 passed with one uploaded tiny text file and Terra in Auto: correct private code retrieved through file search; request idempotency and stream resume passed; server snapshot matched the uploaded record and survived detachment plus deletion. Final run charged 7,987 units within its 50,000 cap. A preceding run charged 8,009 units and verified retrieval but stopped at an incorrect harness assertion expecting the unsuffixed upload name; the existing upload service suffixes filenames. Both runs cleaned up their own synthetic data. No paid beta generation.

All four local rendered history cases passed (390/1440 px, EN/light and RU/dark). Screenshots reviewed after animations settled. Existing selection/indexing scripts passed too. Beta merge validation: 412 frontend tests/build and 26 backend document tests passed.

The deployed frontend passed all four browser cases through the existing owner canary route, with screenshots reviewed. Pending: public-route confirmation, notice xw0e1f2a3b52 publication, then beta deployment. Announcement checks: six tests, single Alembic head, bounded offline SQL and whitespace passed.
