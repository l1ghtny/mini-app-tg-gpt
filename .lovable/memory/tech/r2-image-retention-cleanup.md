---
name: R2 image retention cleanup
description: Reference-aware cleanup rules for generated and uploaded image objects.
type: ops
---

## Context

R2 image objects outlived deleted chats and metadata expiry. The legacy derived
image cleanup only removed unused conversion originals and did not enforce
normal partial/free/paid retention.

## Decision

- Delete only managed `images/partial`, `images/free`, and `images/paid`
  objects after their configured retention period when no database reference
  remains.
- Delete detached `image_asset` objects only after a 48-hour grace period and
  after rechecking that every row remains detached and no message or derived
  record references the key.
- Verify R2 size and ETag immediately before deletion and update asset metadata
  only after successful object deletion.
- Never automatically delete the ambiguous legacy `tg-bot-images/` namespace.

## Operations

The existing six-hour `cleanup-derived-images` CronJob runs
`jobs.cleanup_derived`, which also invokes the general image cleanup. Keep the
batch limit and dry-run controls available through environment variables.

The 2026-08-04 audit and cleanup evidence is in
`docs/operations/r2-image-storage-audit-2026-08-04.md`.

## Delivery behavior

- For a tracked `ImageAsset`, the authenticated image proxy must read the R2
  object directly by `bucket` and `key`; do not hairpin through the public image
  hostname.
- Treat an R2 `NoSuchKey` or equivalent 404 as a durable missing object: mark
  the asset `missing` and return HTTP 410.
- Client UI must collapse expired and missing objects into a quiet generic
  unavailable state. Retention policy names and object-store details are not
  user-facing error copy.
- Keep the public-URL HTTP proxy only for legacy or otherwise untracked image
  URLs.
