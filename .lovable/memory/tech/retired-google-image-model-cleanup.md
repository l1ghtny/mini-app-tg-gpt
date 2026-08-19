---
name: Stable Google image model endpoints
description: Retired Google image endpoints are compatibility aliases only and persisted data must use the stable Gemini image model IDs.
type: tech
---

## Context / problem

Google retired `gemini-2.5-flash-image` and the preview endpoints
`gemini-3.1-flash-image-preview` and `gemini-3-pro-image-preview`. Active calls
must use `gemini-3.1-flash-image` and `gemini-3-pro-image`.

Because image entitlements and usage accounting are keyed by `model_name`, simply hiding the model is not enough. Persisted defaults and historical ledger rows must be migrated.

## Decision taken

- Keep only `gemini-3.1-flash-image` and `gemini-3-pro-image` in the active Google
  image registry and defaults.
- Canonicalize all three retired endpoint IDs to their stable replacements at
  API and internal service boundaries.
- Use a forward migration to rewrite:
  - `app_user.default_image_model`
  - `conversation.image_model`
  - `request_ledger.model_name`
  - `tokenusage.model_name`
  - tier and usage-pack image entitlement rows
  - image catalog and pricing rows
- Preserve unlimited tier limits when duplicate legacy and stable entitlement
  rows are merged.
- Treat stable Pro 512 requests as 1k. Flash supports 512 directly; both models
  retain the existing 1k and 2k product choices.

## How to apply it in future changes

- Do not reintroduce retired image models into active registries, defaults, or
  catalog seed data. They may remain only in compatibility mappings, tests, and
  immutable historical migrations.
- If an image model is removed in the future, rewrite historical `request_ledger` rows to the replacement model when quota calculations depend on `model_name`.
- When replacing Google image models, verify provider-alignment flows in both user settings and conversation settings.

## Constraints / gotchas

- Historical migrations still mention retired models because they must remain
  immutable; cleanup happens in the later forward migration
  `xj7e8f9a0b1c`.
- Google image resolution identifiers are case-sensitive at the provider
  boundary: send `512`, `1K`, or `2K` as appropriate.
- If the frontend hardcodes image picker options outside the backend catalog contract, that repo needs a parallel cleanup.
