# Image completion hotfix

Owner authorized implementation and production/beta deployment after investigating
backend7Q/frontend5R. A successful image edit was saved, but an empty optional Luna
follow-up failed the entire task.

## Scope and invariants

The stream marks an image delivered only after its consumer resumes past
`image.ready`. A planned task with a delivered image can finish on `empty_answer`
without another provider or image call. Empty text-only tasks, explicit OpenAI
refusals, incomplete/capped responses, required tool failures, image failures and
persistence failures retain their existing failure paths. The follow-up attempt
retains its failed usage record and zero customer units; successful image/routing
attempts settle once. SSE/API shapes are unchanged; no frontend changes required.

## Release gate

What's New: required, because a visible successful-image workflow no longer reports
failure. Publish one shared EN/RU notice after production feature verification;
the first implementation deployment intentionally carries no announcement migration.
Stable notice ID: `2026-10-05-image-completion`.

Backend version: deployed 2.3.1 -> 2.3.2, compatible completion fix.
Frontend version: deployed 2.3.0 unchanged; existing frontend build is reusable.
Keep the shared major at 2. Preserve beta Work and its disabled generation-policy
activation setting. Carry identical announcement history into beta after publishing
through production, the sole migration writer for the shared database.

## Validation before release

80 focused provider, image, moderation and no-hold tests passed on a disposable
local PostgreSQL database. Coverage includes image plus blank/no follow-up text,
text-only failure, explicit refusal, saved legacy plans, consumer interruption
before persistence acknowledgement, exactly one image dispatch and idempotent
settlement. Ruff and `git diff --check` passed.

Frontend source reference confirms existing image/done parsing and recovery remain
compatible. Production flow 9950/#126 and beta flow 9945/#232 passed all four
child jobs. Production deployed merge a2de1d3; beta deployed scoped cherry-pick
89c6b5f. Both served backend 2.3.2. Production generation policy remains enabled;
beta retains its disabled policy and Work functionality.

One actual low-quality image edit in each environment produced a persisted image
with zero text and a terminal done, with no error. Duplicate request IDs reused
the same assistant; completed conversation resume returned 204; Last-Event-ID
replay succeeded. Desktop images rendered through the existing proxy fallback.
The legacy direct app.lightny.ru image host timed out/returned 504; the proxy
returned 200 with 765903 production / 754641 beta image bytes.

Production reproduced the exact empty follow-up: final attempt failed with
empty_answer, 48 supplier units and zero customer units, while the overall request
completed and settled once (8213 image units / 289 Luna units). Beta's image-only
follow-up recorded a completed four-token attempt; overall completion and one
settlement also passed (8208 image units / 332 Luna units). Each made one image
call. The original customer request remains failed/refunded with zero charges.
Received Sentry releases and final notice deployment verification remain pending.

## Pending notice copy

EN title: Images complete without extra text

EN body: If an image is created or edited successfully, the request now completes
even when the AI adds no text after the image. Your image stays in the chat.

RU title: Готовые изображения больше не вызывают ошибку

RU body: Если изображение создано или отредактировано, запрос завершится успешно,
даже когда ИИ не добавит текст после картинки. Результат останется в чате.

No CTA. Additive, idempotent data migration only after live verification. No
retrospective charges or changes to the failed customer request.
