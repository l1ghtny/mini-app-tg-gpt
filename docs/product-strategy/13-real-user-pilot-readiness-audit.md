# Real-user pilot readiness audit

Last updated: 2026-07-27

## Executive verdict

Lightny is **not ready for an unsupervised public web launch**, but it is much closer to a **controlled 5–10 person alpha** than the current product anxiety suggests.

The core product is no longer the main blocker. Local inspection verified a coherent task-first home, SEO prompt handoff through authentication, visible request quotas, Projects, document management, personalization, subscription comparison, payment self-service foundations, resumable streaming code, idempotent accounting, and canary infrastructure.

The gap is the product shell around that core:

- production deployment and end-to-end proof;
- durable browser sessions and complete account recovery/linking;
- privacy, deletion, retention, operator, and support surfaces;
- acquisition and pilot-cohort attribution;
- real application health checks and recovery drills;
- removal of capabilities the UI exposes but the backend does not execute;
- evidence that the research/document promise produces a better result for target users.

The correct next milestone is not “finish the whole competitive roadmap.” It is:

> Invite 5–10 named users into a monitored alpha where they can log in, finish one target workflow, recover from ordinary failures, understand their data and quota, and reach support without founder intervention.

## What changed since the earlier audits

Several previous gaps are now locally implemented or materially improved.

| Earlier gap | Current evidence | Status |
| --- | --- | --- |
| Browser authentication | Email magic-link login works locally with explicit confirmation and HttpOnly cookie sessions | Implemented locally; production matrix still unverified |
| SEO prompt handoff | `?prompt=` survives login, is length-limited, removed from the URL, and prefills without auto-send | Implemented and locally verified |
| Task-first model guidance | Five workflow presets guide quick answers, writing, comparison, sourced research, and document analysis | Useful first version; not a measured router |
| Cost clarity | The home screen shows the request contract and remaining shared pool before send | Implemented |
| Public catalogue | Backend-owned unauthenticated catalogue endpoint exists | Implemented; public consumers still need migration |
| Workspace value | Projects combine chats, instructions, and reusable project documents; conversation URLs and Markdown export exist | Implemented locally |
| Plan comparison | Four current tiers show real text pools, image energy, document limits, discounts, cancellation, refunds, and receipt expectations | Strong local implementation |
| Browser session hardening | New browser sessions use Secure, HttpOnly, SameSite cookies; callback tokens use URL fragments | Implemented, but lifecycle is incomplete |
| Cost telemetry | Cached input and cache-write telemetry were added | Implemented; provider reconciliation remains |
| Product-copy integrity | Stale Claude plan claims were removed from current subscription UI | Improved |

These changes move the app from “feature prototype” to “credible alpha candidate.”

## What people value and how Lightny currently performs

| User value | Current state | Readiness |
| --- | --- | --- |
| Fast first useful result | Prompt handoff, task presets, editable starters, and a visible composer are strong | Good |
| Low decision effort | Workflows hide some model complexity, while expert controls remain available | Good first version |
| Predictable price and limits | Completed-answer contract and detailed shared-pool plan cards are unusually clear | Strong |
| Reliability and recovery | SSE resume, idempotency, connectivity state, and canary patterns exist | Strong engineering foundation; weak E2E proof |
| Trust and control | No in-app privacy, retention, deletion, export, operator, or general support path | Blocking |
| Persistent access | Browser JWT/cookie lifetime defaults to four hours and there is no refresh-session or server-side revocation model | Blocking |
| Work that remains useful | Projects, reusable files, chat links, search, personalization, and Markdown export help | Good foundation |
| Verifiable research | Sonar modes and web sources exist, but the product lacks a complete research-result object, source-quality controls, and document page citations | Incomplete differentiation |
| Help when something fails | Sentry feedback exists only when configured; there is no dependable in-app support fallback | Blocking |
| Confidence before signup | The app login wall has no brand explanation, free allowance, privacy links, screenshots, or reason to choose web over Telegram | Weak for public acquisition |
| Accessibility and speed | Responsive UI works, but dialog accessibility warnings are present, controls with missing accessible names remain, and no automated accessibility/performance gate exists | Unverified |

## Closed-alpha launch blockers

### P0.1 Deploy the actual current product

The backend and frontend both have large dirty worktrees. Local behavior is not evidence that production users can access these changes.

Done when:

- focused changes are committed independently in the backend and frontend repositories;
- migrations are run against an isolated database before staging;
- backend is deployed first, frontend second;
- canary traffic verifies the browser and Telegram clients;
- production environment values for callback URL, CORS, cookie security, proxy CIDRs, API URL, SMTP/email delivery, Sentry, and support are explicitly checked;
- rollout, rollback, and migration rollback steps are recorded.

### P0.2 Add a real browser E2E gate

The frontend currently has seven unit tests across four files and no Playwright E2E specifications. Backend focused verification passes, but three PostgreSQL identity/integration cases are skipped without `TEST_DATABASE_URL`.

Minimum pilot matrix:

1. new email login;
2. callback confirmation, expiry, replay, and delayed email recovery;
3. reload and logout;
4. prompt handoff through login;
5. first send, stream, reconnect, resume, and final reconciliation;
6. edit, regenerate, delete, and failed-generation refund;
7. project creation, shared instructions, document attach, and project inheritance;
8. image upload/generation and entitlement failure;
9. plan checkout, success, failed payment, recurring consent, cancellation, and refund;
10. Telegram login plus supported linking and conflict behavior.

The test must run against disposable PostgreSQL and Redis, never shared production data.

### P0.3 Replace four-hour bearer-cookie sessions with a browser session lifecycle

`ACCESS_TOKEN_EXPIRE_MINUTES` defaults to four hours. `/auth/me` rotates the token on hydration, but an open browser session can still age out, logout only clears the cookie, and there is no server-side session revocation, device list, or “logout all.”

Done when:

- the web session remains usable over a normal multi-day return pattern;
- short-lived access credentials rotate through a revocable server-side session or equivalent design;
- logout invalidates the server-side session;
- users can revoke other sessions after a lost device;
- expiry and recovery are covered by E2E tests.

### P0.4 Decide the honest web–Telegram account contract

Current linking is one-way for a Telegram-authenticated user attaching an email. A browser-only user cannot initiate Telegram linking. Existing email/Telegram collisions return `account_merge_required`, but no merge, preview, recovery, unlink, or last-identity protection flow exists.

For the alpha, choose one:

- implement explicit link, conflict preview, recovery, unlink, and immutable ledger/subscription rules; or
- test the browser as a standalone surface and remove cross-surface continuity from the alpha promise.

Do not ask real users to discover duplicate histories after the fact.

### P0.5 Publish the minimum trust and support layer

The SEO repository still has no dedicated privacy, terms/public offer, refund, file-processing, contacts, account-linking, or deletion routes. The app settings have no links to these documents, no account deletion/export controls, and no general support destination.

Minimum alpha set:

- operator and contact details;
- privacy notice and beta terms;
- file/provider processing and current retention behavior;
- account, chat, and file deletion instructions;
- payment, renewal, cancellation, and refund terms if payment is enabled;
- one dependable support email or Telegram contact in login, settings, and error states;
- an explicit warning not to upload sensitive production documents until the policies and controls are reviewed.

Sentry feedback can remain as the rich report form, but it needs a visible fallback when Sentry is disabled or fails to open.

### P0.6 Add real health and recovery evidence

The backend Kubernetes probes only test whether TCP port 8000 accepts connections. The Docker image calls `/health`, but no `/health` route exists. This can report a broken API as ready and reports a directly run container as unhealthy.

Done when:

- `/health/live` proves the process is responsive;
- `/health/ready` checks the dependencies required to serve normal requests, especially database and Redis;
- Kubernetes uses the HTTP endpoints instead of TCP-only probes;
- provider outages are reported as degraded without unnecessarily restarting healthy API pods;
- alerts cover auth delivery, provider failure, stream failure, payment webhooks, and elevated 5xx;
- a PostgreSQL backup restore is tested, not merely scheduled.

### P0.7 Make the pilot measurable

Telegram registration records campaign attribution, but new browser magic-link users are created without durable campaign attribution or an explicit `user_registered` event in the inspected flow. SEO CTA clicks and authenticated activation therefore cannot yet be reliably joined.

Minimum pilot funnel:

- invite/source viewed;
- login requested;
- email delivery attempted and failed/succeeded;
- callback confirmed/expired/replayed;
- account created or restored;
- first workflow selected;
- first request sent;
- first useful result completed;
- result copied, exported, shared, or followed up;
- day-2 and day-7 return;
- upgrade opened, checkout started, payment succeeded/failed/refunded;
- support requested.

Persist a first-touch campaign/invite identifier through authentication and attach it to the internal user UUID.

### P0.8 Remove misleading product controls

`code_interpreter` remains in the frontend tool list and request schema, while the backend only accepts the name and does not construct or execute the tool.

Done when it is hidden from every user-visible surface or implemented and covered end to end.

Also keep “Сравнить и выбрать” framed as a decision-analysis workflow. It is not the side-by-side multi-model comparison promised in the longer competitive roadmap.

### P0.9 Define the pilot operation

Before invitations:

- choose one target cohort and three target jobs;
- name the support owner and response window;
- define stop conditions for data loss, double charging, auth failure, or repeated provider failure;
- create a daily review of failed logins, incomplete generations, refunds, support messages, and unexpected cost;
- prepare a short interview script and permissioned session-observation policy.

## Product work required to validate the positioning

These items are not all generic launch blockers. They are necessary if the pilot is intended to validate the “better completed work for research and documents” positioning rather than only generic chat usability.

### P1.1 Turn research into a result, not only a chat answer

- navigable citations and source snippets;
- source date/domain visibility;
- source inclusion/exclusion controls;
- a reusable research result with title, outline, and export;
- visible limitations and incomplete-source states.

Current global products set this expectation: ChatGPT deep research exposes a plan, source controls, progress, interruption, and a cited report; Perplexity Projects combine chats, files, search, source selection, and controlled sharing.

### P1.2 Make document analysis defensible

- page-level file citations;
- PDF visual/OCR/table status;
- exact supported formats and size limits beside upload;
- indexing progress and failure recovery;
- multi-document comparison;
- export to Markdown, DOCX, and PDF;
- deletion and retention controls beside the file.

The current document library is useful, but its empty state mainly shows quotas and provider selection. It does not yet explain the outcome, file handling, or how a user verifies an answer.

### P1.3 Finish model guidance before adding model breadth

Workflow presets are a good first release. Measure whether they reduce model switching, failed requests, and wasted premium allowance. Then add:

- an explanation of why a workflow selected a model/pool;
- a reversible one-request recommendation;
- representative latency and allowance impact;
- side-by-side comparison only when multiple charges are clearly explained.

Claude is useful parity, but it is less urgent than proving that current model guidance improves task completion.

### P1.4 Improve output reuse

Markdown chat export is a good minimum. The next user-value layer is:

- export a selected result rather than the whole conversation;
- preserve citations and source metadata;
- save a result to a Project;
- duplicate/rerun a workflow with new inputs;
- privacy-aware share links with revoke and expiry controls.

## What should not block the first alpha

Do not delay the first monitored cohort for:

- Claude, DeepSeek, or Qwen;
- video, music, voice, or a larger model-count badge;
- native mobile apps or PWA installation;
- team workspaces, SSO, invoicing, or public API;
- autonomous agents;
- public testimonials and large SEO content clusters;
- annual plans, trials, referral rewards, or image-energy boost products;
- side-by-side comparison, if the alpha promise does not mention it.

Competitors already win on catalogue breadth. The alpha should test whether Lightny wins on lower decision effort, predictable quotas, sourced work, reusable Projects, and reliable completion.

## Competitor and category lessons

Snapshot date: 2026-07-27. Recheck volatile claims before public comparison.

- [Chad](https://chadgpt.ru/) reduces purchase anxiety with free entry, visible reviews, mobile and Telegram surfaces, clear cancellation help, and multiple support channels.
- [BotHub](https://bothub.ru/) makes legal documents, model breadth, API, and business controls visible before the user commits.
- [Neuromia](https://neuromia.ru/) combines free credits, pre-generation cost, model recommendation, broad media, and an always-visible support assistant.
- [GPTunnel](https://www.gptunnel.ru/ru/prices) makes pay-as-you-go prices and provider breadth explicit.
- [ChatGPT deep research](https://help.openai.com/en/articles/10500283-deep-research) and [Perplexity Projects](https://www.perplexity.ai/help-center/en/articles/10352961-what-are-spaces) raise the bar from “has web search and folders” to controlled sources, visible progress, reusable results, files, and collaboration.
- [Claude Projects](https://support.anthropic.com/en/articles/9517075-what-are-projects) confirms that chats, files, instructions, and project knowledge are now category expectations rather than unique differentiation.

The practical lesson is not to copy every feature. Mature products consistently reduce uncertainty at five moments:

1. before signup: value, proof, price, free start, and trust;
2. before send: recommended mode, expected cost/allowance, and data movement;
3. during work: progress, cancellation, and recovery;
4. after the result: verification, export, reuse, and sharing control;
5. when something fails: a clear explanation, retry path, refund behavior, and human support.

Lightny is strongest at moment 2 and has a strong technical foundation for moment 3. Moments 1, 4, and 5 need the most product work.

## Recommended release sequence

### Gate A: engineering alpha

- clean commits and isolated migrations;
- browser session lifecycle;
- hide Code Interpreter;
- health endpoints and HTTP probes;
- E2E environment and the critical auth/send/resume matrix.

### Gate B: trustworthy invited alpha

- minimum privacy/beta/file/support documents and in-app links;
- account deletion/export request process;
- explicit web-only or verified cross-surface contract;
- cohort attribution and activation funnel;
- backup restore and rollback drill.

### Gate C: 5–10 real users

Test three jobs only:

1. sourced comparison/research;
2. document analysis with a follow-up;
3. recurring writing or technical work saved in a Project.

Success criteria:

- at least 80% authenticate without direct help;
- at least 70% complete one target job;
- no lost paid request, duplicate charge, or inaccessible result;
- every failure has an understandable recovery or support path;
- at least half of activated users return for a second task within seven days;
- qualitative evidence identifies one workflow users would be disappointed to lose.

These are pilot thresholds, not permanent company KPIs.

### Gate D: public beta

Only after the invited cohort:

- improve the winning workflow;
- add guest/free first value and stronger pre-auth proof;
- publish status/changelog and broader help;
- complete accessibility and performance budgets;
- add the parity features that remove demonstrated objections.

## Evidence inspected

### Local application

- Dummy localhost login with `audit.user+pilot@lightny.test`.
- SEO prompt handoff through login.
- Desktop and 390px mobile home state.
- Workflow presets, shared-pool quota preview, Projects, document empty state, settings, support entry, subscription overview, and plan comparison.
- Browser console warnings, including missing dialog descriptions.

No live AI request, document upload, payment, refund, or destructive account operation was performed during this audit.

### Code and verification

- Backend auth, session, catalogue, project-document, pricing, health/probe, metrics, and deployment code.
- Frontend auth, home, settings, documents, metrics, Sentry, Docker, and test configuration.
- SEO route inventory and previous strategy documents.
- Backend focused tests: `12 passed, 3 skipped`.
- Frontend tests: `7 passed` across four files.
- Current built assets include a roughly 976 KB main JavaScript chunk and a 1.5 MB lazy syntax-highlighting chunk; no enforced performance budget was found.

## Final decision

The app has enough product substance to test. It does not need another broad feature sprint before meeting users.

It needs a focused readiness sprint that makes the existing product dependable, honest, measurable, and supportable. After Gates A and B, invite a small cohort immediately and use their work—not competitor feature counts—to choose the next product tranche.
