# Usage details and mobile settings — M02/M03/Q16

## Behavior

Saved assistant answers gain a lazy Usage action. The owner-scoped API traverses message/conversation ownership, RequestLedger and AllowanceRequest; an explicit DTO exposes customer percentages and quote information only. Reserved/pending requests never masquerade as a confirmed zero. New admissions save quote percentages and both allowance denominators; later grant changes cannot rewrite their displayed percentages. Historical requests show recorded-period percentages with no invented quote. Tiny positive usage stays visibly positive. No reservations, provider spending policy or tool budget changes.

The allowance snapshot exposes Luna remaining percentage independently from shared capacity. Its recovery action opens a new home chat using canonical Luna with tools disabled, no selected/project files and no required search intent, preserving an existing composer draft. It is offered only with known available Luna capacity. Audio keeps its existing separate minutes and reset date.

Mobile Settings starts with six familiar categories and opens focused pages with Back/Close and saved category scroll positions. The desktop navigation retains the same categories. Unsaved AI preferences guard category changes, Back, Close and drawer dismissal; cancel retains edits, discard resets the form. Account data loads when its category is opened. Child account/file dialogs reuse existing flows.

## Release and compatibility

Backend 2.3.1 → 2.4.0; frontend 2.3.0 → 2.4.0. Both are compatible minor capabilities and retain major 2. Schema 67 adds nullable customer_quote JSON only; it does not backfill quotes, mutate balances or change legacy admission. Apply migration before the backend, then frontend. Old UI ignores additive fields; new UI treats missing Luna percentage as unavailable and handles missing usage with retry. Coordinate paired PRs.

## Executed checks

324 backend allowance/API/migration tests passed; Ruff passed. 577 frontend full-suite tests passed, plus the new versioned API test; corrected navigation tests pass. Build passes. Full TypeScript has the unchanged LazySyntaxHighlighter TS2322 baseline. UI acceptance and serial built-in review results will be added before handoff. Synthetic fixture data is not production API proof; no paid generation or payment mutations performed.

## What's New decision

Required for the complete customer-facing workflow; draft only until production acceptance. No migration/feed record created in this implementation PR.

**See usage for each answer**
Open Usage beside an answer to see how much of your allowance it used. Luna replies now show their own remaining allowance, with an action to start a Luna chat without tools. Mobile settings are grouped into focused pages, with protection for unsaved AI preferences.

**Расход на каждый ответ**
Нажмите «Расход» рядом с ответом, чтобы увидеть списание из своего лимита. Для ответов Luna теперь отдельно показан остаток лимита и есть кнопка нового чата без инструментов. Настройки на телефоне разделены на страницы; несохранённые предпочтения ИИ защищены от случайного закрытия.
