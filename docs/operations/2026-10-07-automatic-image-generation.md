# Automatic image generation incident

The generation planner removed image_generation from Auto tools despite the
user enabling all tools. The provider prompt also required a manual image action.
Live read-only evidence confirmed this for the reported account: Auto settings,
positive paid allowance, completed text requests, image_consent=false and no image
tool in their saved plans. No customer history or balances were changed.

The fix allows image generation when tool permissions and paid allowance permit
it, including Auto. Explicit exclusions remain authoritative. Explicit image
actions and optional image estimates at least 5% of the monthly grant require
signed confirmation. Displayed image estimates use normal prompt sizing while
supplier funding retains the maximum tool-query bound. Per-request caps, single
image operation, durable accounting, ownership and idempotency remain enforced.
Changed permission-policy fingerprints invalidate prior five-minute quotes.

API/SSE shapes are unchanged. Frontend follow-up: none; existing quote and
confirmation handling consumes the existing response fields. Backend version:
2.5.1 -> 2.5.2, compatible fix. Frontend version: 2.5.0 unchanged (verified
serving asset tg-mini-frontend@2.5.0+130). Shared product major remains 2.

What's New: required, because enabled images previously failed in Auto mode.
Publish one notice in a separate migration only after production acceptance.

Pending notice:

- EN title: Image generation in Auto mode
- EN body: Ask to create or edit an image directly in the chat when image tools
  are enabled. Auto mode can now use them without selecting Create image for
  each reply. Spending limits and confirmation for expensive images still apply.
- RU title: Изображения в режиме «Авто»
- RU body: Если генерация изображений включена в настройках инструментов,
  попросите создать или изменить картинку прямо в чате. В режиме «Авто» больше
  не нужно выбирать «Создать изображение» перед каждым ответом. Лимиты расхода
  и подтверждение для дорогих изображений сохраняются.

Validation and deployment evidence will be appended after execution. The
reported user's original request is not replayed; acceptance uses a separate,
clearly marked synthetic chat on the owner's existing account.
