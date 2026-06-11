---
name: Retired Google image model cleanup
description: `gemini-2.5-flash-image` is retired from active backend selection and must be rewritten to `gemini-3.1-flash-image-preview` in persisted data.
type: tech
---

## Context / problem

The older Google image model `gemini-2.5-flash-image` caused provider-alignment flows to coerce users onto an outdated image choice, which in turn dragged Google text selections onto broken or unsupported combinations.

Because image entitlements and usage accounting are keyed by `model_name`, simply hiding the model is not enough. Persisted defaults and historical ledger rows must be migrated.

## Decision taken

- Remove `gemini-2.5-flash-image` from the active backend image model enum and provider registry.
- Keep only a legacy alias mapping to `gemini-3.1-flash-image-preview` for internal canonicalization safety.
- Use a forward migration to rewrite:
  - `app_user.default_image_model`
  - `conversation.image_model`
  - `request_ledger.model_name`
  - tier and usage-pack image entitlement rows
- Delete the retired model from active image catalog and pricing tables.

## How to apply it in future changes

- Do not reintroduce retired image models into active registries, defaults, or catalog seed data.
- If an image model is removed in the future, rewrite historical `request_ledger` rows to the replacement model when quota calculations depend on `model_name`.
- When replacing Google image models, verify provider-alignment flows in both user settings and conversation settings.

## Constraints / gotchas

- Historical migrations still mention the retired model because they must remain immutable; cleanup happens in a later forward migration.
- If the frontend hardcodes image picker options outside the backend catalog contract, that repo needs a parallel cleanup.
