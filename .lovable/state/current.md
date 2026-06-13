# Current State

## Current objective

Ship the current `development` backend state, including the Gemini image handoff fix, the R2 proxy bypass, the Redis-safe image stream publish fix, and the image-asset deletion fix, as the next production release.

## In progress

- Consolidate the staged and unstaged backend changes in the main `development` checkout.
- Prepare a releasable commit and push it so TeamCity can build a deployable backend image.
- Determine the correct TeamCity build/deploy path for a production rollout.
- Verify the new image-asset detach behavior against local edit/delete flows once test execution is available again.

## Completed

- Verified the Gemini image generation regression was caused by the backend needing function-call handoff to the Google image model.
- Ported the Gemini image handoff fix into the main `development` checkout.
- Ported the R2 client proxy bypass so Cloudflare R2 requests do not inherit the Google WARP proxy.
- Adapted the current image pipeline to surface final image storage failures as structured stream errors while ignoring partial-image persistence failures.
- Fixed the post-generation publish crash by normalizing Redis stream events before `XADD`, so nested payloads are JSON-encoded and `None` fields are omitted.
- Fixed message edit/delete flows to detach `ImageAsset.message_content_id` before deleting linked `MessageContent` rows, preventing FK violations when truncating image-bearing messages.
- Confirmed the combined backend tree passes targeted tests:
- `tests/test_google_interactions.py`
- `tests/test_event_bus.py`
- `tests/test_r2_client.py`
- `tests/test_image_pipeline.py`
- `tests/test_image_asset_retention.py`
- `tests/test_image_proxy_route.py`
- `tests/test_premium_samples.py`
- `tests/test_conversion_state_endpoint.py`
- Confirmed the checked-in deploy scripts only retag Kubernetes to an existing image and therefore require a TeamCity build from a pushed commit.

## Blockers and risks

- The current release candidate exists only in the local working tree until it is committed and pushed.
- Deployment cannot include these fixes until TeamCity builds a new backend image tag from the updated branch.
- The working tree still includes `.lovable` state noise and local attachment files that should not be part of the release commit.
- Local manual verification is still needed against the UI stream after the Redis publish fix, and the new image-asset delete/edit regression tests could not be executed because shell escalation hit a usage-limit rejection.

## Next steps

- Re-run one local image-generation request through the UI and confirm the stream now reaches `image.url`/`done` instead of `GOOGLE_UPSTREAM_UNAVAILABLE`.
- Re-run local message delete and edit flows on conversations containing generated images and confirm they no longer 500 on `image_asset_message_content_id_fkey`.
- Stage only the intended backend code, tests, and migrations for the release commit.
- Commit and push the consolidated `development` branch changes.
- Queue the backend build and deploy jobs in TeamCity against `development`, then monitor rollout status.
