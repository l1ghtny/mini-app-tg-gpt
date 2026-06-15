# Current State

## Current objective

Align welcome-bonus and recurring image-energy limits with daily reset semantics, make 5-day accumulation start from subscription grant time, and expose unambiguous reset timestamps to the frontend.

## In progress

- Backend implementation and focused verification are complete.
- Frontend still needs to surface the daily-limit wording and exact reset timing in the subscription and energy UI.

## Completed

- Kept free recurring subscriptions without a synthetic monthly `expires_at`, so daily-reset UI is no longer polluted by fake expiry dates.
- Updated recurring image pacing to start with one day of energy at `UserSubscription.started_at` and accumulate toward the 5-day cap from that point.
- Wired `started_at` through entitlement selection and image-energy usage responses so new users do not receive a full 5-day bank immediately.
- Fixed image-energy integer reporting so `used_energy` reflects the accrued bucket instead of the full burst cap.
- Made `next_reset_at` explicit UTC in usage and premium-sample responses.
- Added and updated regression tests for day-0 energy, 5-day accumulation over time, entitlement fallback, and UTC daily reset timestamps.

## Blockers and risks

- Frontend currently shows relative reset text in some places and does not clearly communicate that welcome-bonus text/image limits are daily.
- Existing frontend parsing already accepts the response shape, but the UI copy still needs to be adjusted to use `next_reset_at` explicitly.

## Next steps

- In the frontend, surface the welcome-bonus daily text and image limits in the subscription overview instead of only showing remaining amounts.
- Show reset timing as an exact local time derived from `next_reset_at`, with UTC wording where helpful.
- If desired, follow up with a small frontend pass to replace relative-only “resets in …” labels with “resets at … local time” text in the main energy surfaces.
