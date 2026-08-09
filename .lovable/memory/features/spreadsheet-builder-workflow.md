---
name: Spreadsheet builder workflow compatibility
description: Add broader spreadsheet workflows without renaming historical comparison records.
type: feature
---

## Context

The first durable workflow was persisted as `offer_comparison_xlsx`, but its engine actually provides reusable CSV/XLSX loading, column normalization, workbook rendering, provenance, revisions, accounting, and private artifact storage.

## Decision

- Keep `offer_comparison_xlsx` immutable for historical runs, retries, revisions, metrics, and downloads.
- Add `spreadsheet_builder_xlsx` as a separate persisted kind.
- Accept 1–5 CSV/XLSX files, require a user goal, and treat preferred columns as optional guidance backed by source data.
- Reuse the durable execution path while selecting workflow-specific workbook labels, artifact kind, and filename.
- Keep policy and budget values in `work_run_policy`; enabling a new kind is DB configuration, not an environment flag or schema change.

## Constraints

- Never rewrite stored kind values or overwrite parent artifacts.
- Never invent preferred columns that source data cannot support.
- Beta must not run migrations against the shared production database.
- The UI must continue to expose historical comparison artifacts after the broader workflow becomes primary.
