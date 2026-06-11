# Current State

## Current objective

Support the conversion-focused backend rollout by keeping the TBank recurring-payment contract verified and aligning first-touch bot messaging with the premium Telegram positioning.

## In progress

- Break down the next backend conversion work: payment-confidence support APIs, premium-sample support, and attribution-aware post-purchase flows.

## Completed

- Read the existing recurring-payment handoff doc and TBank compliance memory note.
- Verified the backend exposes bind-first recurring endpoints under `/api/v1/payments/tbank`.
- Confirmed card binding uses `AddCard` / `GetAddCardState` and persists `RebillId` as the saved method token.
- Confirmed SBP account binding uses `AddAccountQr` / `GetAddAccountQrState` and persists `AccountToken` as the saved method token.
- Confirmed binding itself does not activate the subscription; activation only starts after `POST /activate-bound`.
- Confirmed saved payment methods support list, set default, detach, and retry-renewal flows.
- Verified focused payment and subscription tests locally: `15 passed` across `test_payment_binding_flow.py`, `test_payment_discounts.py`, and `test_user_subscription_active.py`.
- Added a local-testing note to the frontend handoff doc explaining that bind completion can be tested locally via polling, but final payment confirmation still depends on webhook delivery.
- Added a durable tech memory note for the TBank local webhook testing constraint.
- Rewrote the bot `/start` and fallback nudge copy in `app/bot/bot_main.py` to match the current positioning:
  - premium AI inside Telegram;
  - GPT + Gemini, images, and file workflows;
  - pay in rubles / no VPN friction;
  - stronger ad-entry copy for campaign traffic.
- Verified the updated bot message file compiles with `poetry run python -c "import py_compile; py_compile.compile('app/bot/bot_main.py', doraise=True)"`.

## Blockers and risks

- Full localhost activation still needs the TBank webhook to reach the backend; otherwise payment status will stay in the backend's pending state.
- The frontend repo is not writable from this workspace, so UI implementation itself remains a follow-up for the frontend team.

## Next steps

- Hand the verified API contract and localhost testing caveat to the UI team.
- Define the backend `conversion-state` payload needed by the new frontend paywall/onboarding plan.
- Design the v1 premium-sample backend flow with explicit daily eligibility and auditable consumption.
- If full local end-to-end activation is needed, provide a public tunnel or replay path for `/api/v1/payments/tbank/webhook`.
- After frontend integration, run one real bind -> activate -> webhook -> active-subscription flow against the TBank demo terminal.
