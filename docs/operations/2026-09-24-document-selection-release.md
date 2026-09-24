# Document selection and retrieval repair — 24 September 2026

User authorized production first, then beta. Isolated clones preserve unrelated working-tree changes.

## Scope

- The chat badge previously refreshed when the picker opened and periodically while open, but not when selection saved. Refreshes could arrive after newer saved state. Saved selection now notifies the composer immediately, close refreshes reconcile it, and stale reads cannot overwrite newer picker/manager state.
- Live owner metadata confirmed a ready attached document with conversation `tool_choice=[]`. Attaching documents now enables `file_search` atomically with the document links, preserves other permissions, and returns the saved tool choice. Frontend applies it before completing selection, including new chats. A subsequent explicit no-tools message still disables tools.
- Shared chat incorrectly treated the OpenAI `allowed_tools` envelope as a required tool name. The adapter now interprets permissions and requirements separately for both OpenAI and Anthropic. Legacy `['auto']` also retains automatic tool access.
- Reupload attachment failures are surfaced rather than swallowed.

## Local validation

- Frontend: 369 tests pass; production build and focused ESLint pass. Desktop 1440 px / mobile 390 px Chrome checks cover selecting two files, badge changes, reopening, and removing both in four saves, with no uncaught errors or horizontal overflow.
- Backend: 126 allowance tests pass against private test schemas, including nine document regressions for permission preservation, tool routing and retrieval from two stores. Ruff passes with the existing SQLAlchemy `E712` warning excluded; whitespace checks pass.
- Full TypeScript check reports the existing `LazySyntaxHighlighter.tsx:30` lazy-component fallback prop inference error. That file is unchanged; the production build passes.

## Release gate

What's New: required. Users can now reliably attach/detach files and ask about their contents. One EN/RU announcement will be published only after production functionality is verified. Backend master remains the sole writer of the shared feed.

Draft EN: **Documents stay attached and ready to use** — Select uploaded files from the paperclip menu to use them in your chat. The attachment count now updates as soon as your selection is saved, and attaching a file enables document search. You can also remove files from the chat on the first try.

Draft RU: **Документы прикрепляются и доступны в чате** — Выберите загруженные файлы в меню со скрепкой, чтобы работать с ними в чате. Счётчик вложений теперь обновляется сразу после сохранения выбора, а при прикреплении файла включается поиск по документам. Убрать файл из чата тоже можно с первой попытки.

## Next

Release production, verify real upload/index/select/two-document retrieval/deselect and settlement, publish announcement, then integrate and verify beta. No customer documents or conversations are to be changed by acceptance tests.
