# Image input preparation

Managed image uploads are stored unchanged. Before a new message or replayed
history is sent to the model, `ensure_openai_compatible_image_url` checks the
actual image dimensions and format in a worker thread.

- Supported static images remain byte-for-byte unchanged when they fit within
  65,535 pixels per side and 30,000 patches (`ceil(width / 32) * ceil(height / 32)`).
- Oversized images are reduced only enough to satisfy both limits, preserving
  aspect ratio without upscaling. Integer dimension checks account for patch
  rounding, including very long screenshots and panoramas.
- Converted/resized copies use Lanczos resampling, normalize EXIF orientation,
  preserve transparency in PNG/WebP, and use JPEG quality 95 without chroma
  subsampling. PNG/WebP output is lossless after resizing. Animated inputs are
  represented by their first frame; the original animation is retained in storage.
- Variants are cached in `DerivedImage`; no message URL or original object is
  replaced. Existing expiry, readiness, and missing-object checks still apply.
- Model image detail stays at its existing default. There is no forced `low`
  setting or blanket 2048px downscale. Models may still apply their own resizing
  and token limits. More retained detail can consume more image tokens than a
  blanket 2048px policy.

Source: [OpenAI images and vision requirements](https://developers.openai.com/api/docs/guides/images-vision#image-input-requirements).

The 30,000-patch policy is fixed for cached variants using `max_side=65535`.
Invalidate/version those variants if the budget is changed. Earlier production
conversion variants use `max_side=2048` and are not reused by the new default.

Every image must resolve to an ImageAsset owned by the authenticated user in the
configured R2 bucket. Known domain aliases can resolve by owned bucket/key;
unknown URLs, raw keys, data URLs, and another user's assets return 403 before
R2 access or model submission. New sends prepare images before message/ledger
creation; edits prepare them before changing history. History replay checks
ownership again and omits inaccessible older images. Untracked legacy images
must be reattached through the upload endpoint.

The upload endpoint verifies actual image content, enforces a 20 MiB file limit
and Pillow's decoded pixel ceiling, and rewinds the unchanged original for R2.
The send schema accepts only user-role text/image content; the historical `image`
alias is normalized to `image_url` before authorization.

Frontend contract: the existing Composer upload and `buildOutgoingContent` in
`src/lib/api.ts` already use this flow. No frontend changes are required for normal
attachments. Direct clients must upload first and send an owned returned URL;
invalid uploads receive 400/413 and unauthorized attachments receive 403.

## Validation and release handoff

`tests/test_image_resizing.py` uses real Pillow image bytes and mocked R2/database
operations. It includes a JPEG requiring exactly 48,960 patches, supported and
converted formats, EXIF, transparency, cache reuse, unavailable assets, and the
shared new-message/history preparation path. It makes no provider requests.

The focused suite includes crafted POST/PUT requests against isolated PostgreSQL,
foreign-user R2 URLs, domain spoofing, data/loopback URLs, unknown parts and forged
roles, with assertions that rejected requests create no messages or ledger rows.

What's New: required for this user-visible image reliability fix. The current
production frontend and backend release scope was checked; there is no frontend
source change in this release. Existing feed entries contain no duplicate notice.
Deploy and verify the feature first, then publish one backend-master announcement
migration and carry that same migration into beta (shared database, one writer).

Pending announcement:

- EN title: Automatic resizing for large images
- EN body: Attach a photo or screenshot in chat as usual. Images that exceed the
  model's size limit are resized automatically, preserving as much detail as
  possible. Your original upload stays unchanged.
- RU title: Автоматическое уменьшение больших изображений
- RU body: Прикрепляйте фото и скриншоты к сообщению как обычно. Если разрешение
  слишком велико для модели, мы автоматически уменьшим изображение, сохранив как
  можно больше деталей. Исходный файл останется без изменений.
- No CTA: attachment entry is the existing chat composer.

Release implementation and deployment evidence will be recorded in the state
checkpoint. Publication is deferred until the feature is verified in production.
