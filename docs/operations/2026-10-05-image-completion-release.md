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
compatible. Real deployed image send/stream/reload, beta behavior, rollout and
received Sentry release verification remain pending until deployment.

## Pending notice copy

EN title: Images complete without extra text

EN body: If an image is created or edited successfully, the request now completes
even when the AI adds no text after the image. Your image stays in the chat.

RU title: Готовые изображения больше не вызывают ошибку

RU body: Если изображение создано или отредактировано, запрос завершится успешно,
даже когда ИИ не добавит текст после картинки. Результат останется в чате.

No CTA. Additive, idempotent data migration only after live verification. No
retrospective charges or changes to the failed customer request.
