---
name: Work-run artifact previews
description: New spreadsheet artifacts have a bounded private preview beside the complete downloadable workbook.
type: feature
---

## Context

Downloading was the only way to inspect a completed workflow result. Embedding spreadsheet contents in work-run history would make normal history requests large and unnecessarily expose result data.

## Decision

- Generate the preview from the same normalized row matrix as the XLSX renderer.
- Store it as a private JSON sidecar next to the workbook in R2.
- Load it only through an authenticated, ownership-scoped artifact endpoint.
- Treat the workbook and preview as one ready unit during durable storage reconciliation.
- Advertise preview availability through public artifact metadata; keep storage checksums in underscore-prefixed internal metadata.

## Bounds

- 100 preview rows.
- 30 preview columns.
- 500 characters per text cell.
- 2 MB maximum downloaded sidecar size.
- Legacy artifacts without `preview_available` remain valid and download-only.

## Future use

Reuse the artifact preview surface for new tabular workflows. Do not add result contents to list/history responses, expose R2 preview keys, or claim that a legacy artifact has a preview.
