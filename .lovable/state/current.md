# Current objective — 2026-10-05 usage details and settings

First batch Q06/Q09/Q07/focused M09 is merged and deployed: backend 2.3.1, frontend 2.3.0. Production flow9924/#125 and beta flow9919/#231 succeeded with all child jobs. Shared schema 66; rollout grace touched 22 files once, later jobs zero. Production cleanup scheduler enabled and completed a scheduled run; synthetic private-object create/head/delete/404 proof passed. Authenticated production/beta file views and published notice verified. API/bot Sentry release configuration verified at runtime; received 2.3.1 events not yet observed, so do not claim that layer passed.

Phase two M02/M03/Q16 is implemented in isolated codex/usage-details-settings-20261005 branches. Customer per-answer usage links the owned answer to its settled ledger, keeps supplier costs private, stores admission quote denominators, and distinguishes pending from zero. Separate Luna capacity/recovery and audio minutes stay independent. Mobile settings uses the desktop categories, focused pages and dirty AI preference guards.

Validation: 324 backend regressions, Ruff and migration upgrade/downgrade proof pass. Frontend 577 full-suite tests plus the added API-route regression and corrected navigation tests pass; production build passes. TypeScript retains only the pre-existing LazySyntaxHighlighter error. Rendered EN/RU desktop/mobile checks and built-in reviews are in progress. No paid calls or production user mutations for phase two.

Versions: both 2.4.0, compatible minor from deployed backend 2.3.1/frontend 2.3.0. What's New required, draft in docs/ux-review/2026-10-05-usage-details-settings.md; no feed row before production availability.

Next: finish UI acceptance, run serial built-in reviews, fix findings, create linked PRs. Apply schema 67 before deploying the new backend and deploy backend before frontend. Keep legacy responses explicitly unavailable or based on their recorded allowance period; do not fabricate old quotes. Preserve beta Work and primary dirty changes. Reassess remaining launch gaps after this scope is reviewable.
