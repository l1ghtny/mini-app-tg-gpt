# M16 search context backend companion

Exact normalized titles precede other results, including stronger semantic or prefix matches. Duplicate exact titles remain separate. Other semantic scoring and ordering are preserved.

The existing search endpoint returns the same conversation fields plus optional typed `search` metadata: a plain excerpt of at most 240 characters, message id/date, and project name of at most 120 characters. Metadata lookup considers at most three indexed passages per result and uses bounded batch queries. A live join verifies conversation ownership, message ownership/type, and that the indexed passage still occurs in the current message text. Deleted, truncated, rewritten or forged index content cannot supply an excerpt. Conversation summaries never supply excerpts.

Search request URLs are redacted from Sentry events, breadcrumbs, transactions and logs. No query or excerpt logging is added; embedding/model/index behavior is unchanged.

## Validation

Five database regression cases on disposable PostgreSQL cover exact titles in English/Cyrillic/long text, duplicate preservation, current-message ownership and edits/deletes, legacy field retention, semantic-only excerpts, unchanged nonexact ordering and bounded queries. A pure excerpt check also passes. Two offline pytest checks cover excerpt formatting and telemetry redaction; the excerpt check overlaps the database runner's pure check. Ruff and Python compilation pass.

The local runtime lacks `pg_trgm`: the database runner stubs only `similarity(text,text)` to zero. Controlled semantic vectors and exact/prefix competitors exercise ordering; real PostgreSQL live joins and current-text matching run unchanged. Full trigram integration and deployed acceptance are unverified.

## Frontend contract and review boundary

Frontend companion files: `src/types/index.ts`, `src/lib/api.ts`, `src/hooks/useSidebarSearch.ts`, `src/components/ConversationSidebar.tsx`, `src/components/SearchResultText.tsx`, `src/lib/searchTelemetry.ts` and `src/main.tsx`. It is stacked on Q20. Metadata is kept only in active search state and stripped before storing a selected conversation; an old backend leaves title-only rows working. Deploy the compatible backend first when the reviewed combined release is authorized.

This is an unmerged, unreleased review PR. No migration, version bump or announcement is included. Q07, Q11 and public-share creation/viewing remain deferred. The future Q04/Q19/Q20/M16 release requires one grouped EN/RU notice after feature verification, and independent compatible minor-version decisions against then-live versions while retaining a shared product major.
