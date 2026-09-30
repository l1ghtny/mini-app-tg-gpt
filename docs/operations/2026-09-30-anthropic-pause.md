# Temporary Anthropic pause

Owner requested a temporary pause because the Anthropic API account cannot be
topped up now. No balance details are exposed to customers.

- `ANTHROPIC_ENABLED` defaults to false. Both catalogs retain Claude entries and
  report `available: false`; other providers remain available.
- New Claude sends are rejected with HTTP 503 / `model_temporarily_unavailable`
  after idempotent-result reconciliation and before message/allowance mutations.
  Estimates, shared execution and each Claude upstream turn enforce the pause.
- The frontend disables Claude choices, shows “Temporarily unavailable” /
  “Временно недоступно”, and preserves drafts while offering the existing model
  selector. Operational unavailability takes precedence over upgrade prompts.
- Restore by setting `ANTHROPIC_ENABLED=true` for production and beta API workloads
  after funding is restored, and roll them through the normal deployment process.
  Refresh the app to reload availability. No plan/model/history data must change.

Release decisions:

- What's New: not required. This is temporary operational provider availability,
  communicated at selection and sending, not a permanent capability or plan change.
- Backend version: deployed `2.0.0` → `2.0.1`; compatible operational change.
- Frontend version: deployed `2.0.0` → `2.0.1`; compatible availability UI.
- API/bot Sentry release IDs include component, version and image build timestamp.
  Frontend keeps the existing matching runtime/source-map version plus build ID.

Validation: 44 focused backend tests and 112 focused frontend tests passed;
production frontend build, targeted ESLint (existing warnings only), Ruff and diff
checks passed. Real component previews at 390px and 1280px in EN/RU verified paused
choices, blocked Send, switching models and draft preservation. Full TypeScript
checking retains the pre-existing `LazySyntaxHighlighter.tsx` prop error.

Deployment and runtime verification: pending.
