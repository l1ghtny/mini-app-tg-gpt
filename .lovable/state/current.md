# Current State

## Current objective

Integrate Perplexity Sonar as a backend text provider for the Telegram mini-app, then hand off the exact frontend changes needed for Lovable.

## In progress

- None.

## Completed

- Fixed migration failure reported during `alembic upgrade head`:
  - root cause: migration used `ai_model_pricing`, but the existing SQLModel table is named `aimodelpricing`
  - patched `l1a2b3c4d5e6_add_perplexity_sonar_models.py` to seed/delete from `aimodelpricing`
- Added Perplexity config:
  - `PERPLEXITY_API_KEY`
  - `PERPLEXITY_API_BASE_URL`
  - `PERPLEXITY_SEARCH_CONTEXT_SIZE`
- Added `sonar` and `sonar-pro` to the backend text model registry under provider `perplexity`.
- Added `app/services/perplexity_service.py` to call Perplexity Sonar through OpenAI-compatible chat completions and normalize stream output into existing SSE event types.
- Wired `stream_normalized_ai_response()` to route Perplexity models to the new provider adapter.
- Kept Perplexity text-only:
  - rejects image input
  - rejects image generation
  - keeps file search restricted to OpenAI
  - uses the existing OpenAI image default for conversation/settings compatibility
- Added migration `l1a2b3c4d5e6_add_perplexity_sonar_models.py` to seed:
  - text model catalog rows
  - provider pricing rows
  - tier limits
  - usage pack limits where matching source limits exist
- Added focused tests in `tests/test_perplexity_provider.py`.
- Added durable memory note `.lovable/memory/tech/perplexity-sonar-provider.md`.

## Verification

- `poetry run alembic upgrade head`
  - passed locally after the pricing table-name fix
- `poetry run pytest tests/test_perplexity_provider.py tests/test_google_provider_contracts.py tests/test_user_settings_endpoint.py tests/test_models_catalog_endpoint.py`
  - passed: 10 tests
- `poetry run pytest tests/test_perplexity_provider.py tests/test_models_catalog_endpoint.py tests/test_text_daily_reset.py tests/test_text_infinite_entitlement.py`
  - passed: 10 tests after the migration fix
- `poetry run pytest tests/test_text_daily_reset.py tests/test_text_infinite_entitlement.py`
  - passed: 5 tests
- `poetry run python -m py_compile app\services\perplexity_service.py app\services\ai_service.py app\services\model_registry.py app\api\chat_helpers.py app\api\model_catalog_helpers.py app\services\subscription_check\realtime_check.py migrations\versions\l1a2b3c4d5e6_add_perplexity_sonar_models.py`
  - passed
- `poetry run alembic heads`
  - current head: `l1a2b3c4d5e6`

## Blockers and risks

- Live runtime validation still requires a real `PERPLEXITY_API_KEY`.
- Perplexity streaming was verified with mocked OpenAI-compatible chunks, not a live API call.
- Frontend must disable image/file controls for Perplexity models before this is exposed broadly.

## Next steps

- Add `PERPLEXITY_API_KEY` in staging/production secrets.
- Run `poetry run alembic upgrade head` during deploy.
- Ask frontend/Lovable to add model picker and tool-control support for provider `perplexity`.
