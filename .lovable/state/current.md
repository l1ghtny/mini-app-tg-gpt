# Current State

## Current objective

Fix the production document upload regression affecting `POST /api/v1/documents/upload`.

## In progress

- Backend patch is implemented locally.
- Local regression coverage is added and passing.
- Production verification still needs a deploy and a fresh Sentry check after release.

## Completed

- Queried Sentry production events for backend project `gpt-mini-app-backend`.
- Confirmed repeated prod failures on June 18, 2026 in release `1.6.1` with:
  - exception: `MissingGreenlet`
  - transaction: `/api/v1/documents/upload`
  - environment: `production_main_server`
  - culprit/location: `app/api/document_helpers.py` / `_active_provider_artifacts`
- Traced the failure to the upload response path:
  - `upload_document()` committed successfully
  - `session.refresh(document)` left `provider_artifacts` unavailable for safe async access in the response serializer
  - `_document_to_response()` then touched `document.provider_artifacts` and triggered a lazy load in the wrong context
- Patched [app/api/document_helpers.py](G:\0\Coding_projects\Python\PycharmProjects\mini-app-tg-gpt\app\api\document_helpers.py) to reload the saved document through `_load_document_for_user(...)` before serializing the response.
- Added a focused regression test in [tests/test_document_provider_fallback.py](G:\0\Coding_projects\Python\PycharmProjects\mini-app-tg-gpt\tests\test_document_provider_fallback.py) covering `upload_document()` returning provider artifacts without a lazy-load failure.
- Validated successfully:
  - `poetry run pytest tests/test_document_provider_fallback.py`

## Blockers and risks

- This session did not deploy the backend, so the fix is only locally validated.
- Sentry issue grouping will still show historical failures until a fixed release is deployed and exercised.

## Next steps

- Deploy the backend patch.
- Re-test a real document upload in production.
- Re-check Sentry for new `/api/v1/documents/upload` events after the deploy.
- If uploads still fail after this fix, inspect the background ingestion task separately for OpenAI vector-store or provider-specific errors.
