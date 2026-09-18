# R2 image storage audit — 2026-08-04

## Scope and safety

- Bucket: `tg-bot-images`.
- Compared the complete R2 object listing with production `image_asset`, image
  `messagecontent`, and `derived_image` references.
- PostgreSQL discovery ran in a read-only transaction.
- Automatic deletion was restricted to managed prefixes and their configured
  retention policy: partial 1 day, free 30 days, paid 365 days.
- Detached asset objects required a 48-hour grace period and zero message or
  derived-image references.
- The legacy `tg-bot-images/` key namespace was excluded from deletion.

## Before cleanup

- R2: 4,122 objects, 6.44 GiB.
- Database: 2,620 image assets, 4,647 image message URLs, 7 derived-image rows.
- Zero-reference objects older than 48 hours: 304 objects, 594.07 MiB.
- Conservative policy-safe orphans: 70 objects, 137.08 MiB.
- Detached asset keys: 165 objects, 334.63 MiB; 162 objects / 332.32 MiB were
  older than the 48-hour grace period.
- Exact cleanup union: 232 objects, 469.40 MiB.
- Existing database references to missing objects: 867. These predated this
  cleanup and remain a separate legacy-repair investigation.

## Backup and deletion

- Downloaded all 232 candidates to
  `/private/tmp/tg-bot-images-cleanup-backup-2026-08-04` before deletion.
- Verified exact key count, byte size, and ETag for every backup object: zero
  missing, extra, or mismatched objects.
- Took a second production snapshot immediately before deletion; its exact key,
  size, and ETag set matched the backup.
- The executor locked asset rows and rechecked message/derived references plus
  R2 HEAD metadata before each deletion.
- Deleted all 232 requested objects, 492,206,153 bytes; zero skipped.
- Marked 162 successfully deleted detached `image_asset` rows as deleted.

## After cleanup

- R2: 3,890 objects, 5.98 GiB.
- Policy-safe cleanup candidates: zero.
- Old partial objects: zero.
- Three recent detached objects remain inside the 48-hour grace period.
- The same 867 pre-existing missing live references remain after excluding the
  162 new deleted-asset tombstones; no live-reference loss was introduced.
- Verified a current active database image with a successful R2 HEAD and a
  positive object size.
- No incomplete multipart uploads were present.
- The available object token cannot read bucket lifecycle configuration, so
  lifecycle-rule state was not independently verified.

## Prevention

- `app/services/image_cleanup.py` implements reference-aware cleanup for:
  - partial objects past partial retention;
  - unreferenced free/paid objects past their configured retention;
  - detached asset objects past a configurable grace period.
- `jobs.cleanup_derived` invokes it from the existing six-hour
  `cleanup-derived-images` CronJob.
- The worker rechecks row/message/derived references and R2 size/ETag before
  deletion, updates successfully deleted asset tombstones, supports dry-run and
  batch limits, and never auto-deletes the ambiguous legacy namespace.

## Remaining work

1. Analyze the legacy `tg-bot-images/` namespace and the 867 pre-existing
   missing references as a separate migration/repair task.
2. Keep the local rollback backup through an acceptance window, then remove it
   after confirming no user-visible regression.
3. Use an R2 admin-read credential to audit lifecycle rules, or document them
   from the Cloudflare dashboard.
