# Current State

## Current objective

Prepare release 1.6.3 and push backend changes to main/master for CI/CD.

## In progress

- None.

## Completed

- Bumped `app/core/version.py` from `1.6.2` to `1.6.3` for the release.
- Investigated Sentry issue `82950975` / `GPT-MINI-APP-BACKEND-P`:
  - endpoint: `POST /api/v1/conversations/{conversation_id}/messages`
  - error: OpenAI `BadRequestError` for invalid `history_summary` JSON schema
  - root cause: `SUMMARY_JSON_SCHEMA` declared `open_tasks`, `constraints`, and `decisions` in `properties` but omitted them from `required` while `strict: True` is used
- Fixed `app/services/openai_service.py` so the strict summary schema requires every declared property.
- Added `tests/test_openai_service_schemas.py` to lock strict-schema requirements for title and summary schemas.
- Added production WhatsNew entry for the mobile text selection release:
  - id: `2026-07-01-mobile-text-selection`
  - kind: `improvement`
  - published_at: `2026-07-01T13:06:00`
  - title_en: `Mobile text selection is easier`
  - title_ru: `Выделять текст на телефоне стало проще`
  - inserted directly into production `whats_new_item` via the backend pod using Unicode escapes for Russian text
- Added production WhatsNew announcement directly to `whats_new_item`:
  - id: `2026-06-25-perplexity-ai-search`
  - kind: `feature`
  - CTA: `open_settings`
  - published_at: `2026-06-25T12:40:13`
  - note: first upsert succeeded but PowerShell mangled Cyrillic; immediately corrected `title_ru`, `body_ru`, and `cta_label_ru` using Unicode escapes and verified stored codepoints
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
- Added Perplexity request controls:
  - `search_mode`: `quick`, `standard`, `deep`
  - `tool_choice`: `fetch_url`
  - `tool_choice`: `finance_search`
- Routed normal Perplexity AI Search through Sonar chat completions and mapped `search_mode` to `web_search_options.search_context_size`.
- Routed explicit `fetch_url` and `finance_search` tool choices through Perplexity Agent API.
- Normalized Agent API output into the same SSE event shape used by the rest of the app.
- Appended Agent API source URLs to assistant text.
- Stored Perplexity Agent API exact usage costs in existing token usage cost columns when `usage.cost` is returned.
- Kept `fetch_url` and `finance_search` scoped to Perplexity models through backend validation.
- Updated `.lovable/memory/tech/perplexity-sonar-provider.md` with the new tool/search-mode contract.

## Verification

- `poetry run pytest tests\test_openai_service_schemas.py tests\test_history_building.py tests\test_perplexity_provider.py tests\test_models_catalog_endpoint.py tests\test_tier_daily_display.py` passed: 22 tests, 1 warning from `google.genai.types`.
- `poetry run pytest tests\test_openai_service_schemas.py` passed: 2 tests.
- `poetry run pytest tests\test_history_building.py` passed: 7 tests, 1 warning from `google.genai.types`.
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
- `poetry run python -m py_compile app\services\perplexity_service.py app\services\ai_service.py app\api\chat_helpers.py app\api\helpers.py app\schemas\chat.py`
  - passed
- `poetry run pytest tests\test_perplexity_provider.py`
  - passed: 7 tests
- `poetry run pytest tests\test_perplexity_provider.py tests\test_google_provider_contracts.py tests\test_user_settings_endpoint.py tests\test_models_catalog_endpoint.py`
  - passed: 13 tests

## Blockers and risks

- Live runtime validation still requires a real `PERPLEXITY_API_KEY`.
- Perplexity streaming and Agent API paths were verified with mocks, not live API calls.
- Frontend must expose Perplexity-only controls carefully; backend rejects `fetch_url` and `finance_search` for non-Perplexity models.

## Next steps

- Monitor CI/CD after pushing 1.6.3 to main/master.
- After deployment, confirm Sentry issue 82950975 stops receiving new events.

## Latest update

- Updated production `text_model_catalog` copy for Perplexity so subscription/usage surfaces describe it as AI Search:
  - `sonar`: `AI Search with sources` / `??-????? ? ???????????`
  - `sonar-pro`: `Deep AI Search` / `???????? ??-?????`
- Corrected the production Cyrillic update using Unicode escapes after raw PowerShell piping mangled Cyrillic output.
- Updated local `model_registry.py` fallback names and the Perplexity seed migration copy to match prod.
