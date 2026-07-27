# App-to-SEO capability audit

Last updated: 2026-07-26

This audit compares the current backend and React application with the public SEO repository. Its purpose is to identify capabilities that already exist but are weakly communicated, claims that need verification before publication, and the next landing and content work that can improve qualified activation.

## Executive finding

The public site explains the model catalogue, AI search, documents, images, pricing, and the web/Telegram split reasonably well. It still presents the product mainly as a capable multi-model chat. The application is closer to a persistent AI workspace: it organizes ongoing work, remembers user and project instructions, restores interrupted generations, keeps a reusable document library, and exposes detailed usage and billing controls.

That workspace layer is the strongest under-communicated product value. It should become the second message after the existing research-and-documents wedge. The landing page should not turn into a complete settings inventory; it should prove three or four workflows, then send users to focused feature pages.

## Capabilities that are safe to communicate now

| Capability in the product | What the public site says now | Better user-facing outcome | Recommended surface |
|---|---|---|---|
| Conversation and folder search | One generic feature card mentions history, search, and folders | Find an old answer or project instead of starting again | Homepage proof block, `/features`, dedicated workspace page |
| Folders with a shared system prompt | Folders are mentioned, shared instructions are not | Set tone, format, and project context once for every chat in a folder | Dedicated workspace page and short homepage differentiator |
| Persistent user personalization plus a guided setup wizard | Not communicated | Teach the assistant how to answer once instead of repeating preferences in every prompt | `/personalization` or a section on the workspace page |
| Rename, move, delete, edit, regenerate, copy, and share | Mostly reduced to “history” and “continue the dialogue” | Keep work editable and reusable rather than treating every answer as disposable | `/features`, help articles, product recording |
| Resumable SSE streams and background generation indicators | Not communicated | Recover an active answer after a short connection interruption and keep track of work in other chats | Reliability proof on `/features`; avoid absolute uptime claims |
| Reusable document library with storage usage, pinning, and chat attachments | Public copy focuses on one-off PDF upload | Upload a document once, pin it, and attach it to the chats where it is needed | Rewrite `/ii-dlya-pdf`; add a real document-library screenshot |
| Per-chat model, thinking, tool, image, and document settings | Model selection is explained, workflow control is not | Use simple defaults or tune a specific conversation without changing the whole account | `/features` and `/models`; keep the homepage concise |
| Automatic or manual tool selection | Not communicated | Let the model choose an available tool, or select search/files/images explicitly | `/features` and an “How tools work” help article |
| Sonar quick, standard, and deep search modes | Explained on model and search pages | Trade speed and resource use for a deeper search when the task requires it | Keep current search content; add an in-product screenshot and example |
| Read a specific URL and finance search for Sonar tiers | Present only in deeper FAQ copy | Ask Sonar to work from a specific page or collect public financial information | Add focused sections to the AI-search page; do not describe it as financial advice |
| OpenAI and Google image models with quality or resolution controls | Generic image capability is communicated | Choose the image model and available quality or resolution for the task | Improve image page with real output comparisons and current controls |
| Image energy with accumulation and visible balance | Pricing mentions energy but does not explain it | See when image capacity is available and how the selected plan affects it | Pricing explainer and help article, not the hero |
| Model-specific usage balance and additional usage packs | Pricing says limits vary | See which pool an action uses and add capacity without changing the core product message | Pricing and billing help pages |
| Payment-method management, retry, cancellation, refund status, and access codes | Mostly absent | Give paying users a clear self-service path when a charge fails or they want to stop | `/billing-help`, `/refunds`, pricing FAQ |
| Passwordless browser login | The site says email magic-link login exists | Start in a browser without creating a password | Hero or CTA support text only after production configuration and E2E validation |
| RU/EN interface, theme, and font-size controls | Not communicated | Make long sessions comfortable and accessible | Help page or settings screenshot; too weak for primary positioning |
| Dynamic chat starters, tutorial, and changelog feed | Not communicated | Start with a useful task and discover new capabilities | Activation flow and `/changelog`; not a landing-page differentiator |

## Suggested Russian feature framing

These are message directions, not final page layouts.

- **Папки с инструкциями для каждого проекта.** Соберите рабочие чаты по темам и один раз задайте правила: контекст, тон и формат ответа.
- **Настройте ответы под себя.** Укажите, чем занимаетесь и как вам удобнее получать результат. Эти настройки будут применяться в новых чатах.
- **Документы остаются под рукой.** Загрузите файл один раз, закрепите его и подключайте к нужным разговорам.
- **Работу можно продолжить.** Приложение умеет восстановить активную генерацию после краткого обрыва связи.
- **Поиск под задачу.** Для простого вопроса выберите быстрый режим, для обзора или сравнения — более глубокий. При необходимости попросите Sonar прочитать конкретную страницу.

The wording deliberately names an action and observable result. Avoid abstract claims such as “единая интеллектуальная экосистема”, “эффективное управление знаниями”, or “бесшовная работа” until there is concrete product proof.

## Do not publish these claims yet

### Code Interpreter

`code_interpreter` appears in the frontend settings and request schema, but the backend tool builder only adds web search, OpenAI file search, and image generation. Selecting Code Interpreter therefore cannot be treated as a working product capability. Remove or hide the option, or implement and test the tool before mentioning it publicly.

### Claude

The frontend subscription outcome copy still says “GPT-5, Claude, Gemini Pro” in Russian and English. Claude is not in the active backend model registry. This is a product-copy bug and a potential purchase-expectation problem; remove Claude from the UI until the provider is actually released.

### Seamless web and Telegram continuity

Both surfaces use the same backend, but separate Telegram and email accounts are not automatically merged. Keep the current qualified explanation until account linking, collision recovery, subscriptions, and history continuity pass production E2E validation.

### Fixed document-retention promises

Document expiration is entitlement-driven and pinned documents behave differently. Publish exact retention periods only from a current public capability source. Do not copy tier strings from the frontend into SEO pages as permanent policy.

### Security and legal absolutes

Do not claim end-to-end encryption, Russian-only storage, confidential processing, guaranteed deletion times, or provider non-retention without a verified policy and contract evidence. The current `/security` page correctly says the complete policy is still pending; that is a launch blocker, not a permanent page state.

### Guaranteed recovery

The stream transport supports resume and reconciliation, but this is not a promise that every generation survives every browser close, provider failure, or deployment. Market the observable recovery behavior, not “answers are never lost.”

## Current landing and SEO gaps

### Product proof

- The homepage uses a constructed chat preview. It needs a visible “example” label or replacement with a real current product capture.
- There are no real desktop and mobile screenshots showing search, document pinning, folder prompts, model controls, or usage balance.
- There is no short recording of the first useful workflow from landing CTA through completed answer.
- There are no permissioned customer cases, testimonials, or repeatable benchmarks.
- Existing examples should show prompt, model, date, settings, output, and limitations when presented as evidence.

### Information architecture

- `/features` is a shallow six-card overview. It needs links to focused pages for workspace organization, personalization, document library, search tools, reliability, and billing help.
- `/help` answers only four topics. It needs task-based navigation, login/account linking, limits, documents, payments, cancellation/refunds, data deletion, and troubleshooting.
- Legacy `-v-telegram` routes still make Telegram look like the product centre. Preserve their rankings, but add web-first canonical routes and intentional redirects or cross-links.
- There is no public `/privacy`, `/terms`, `/public-offer`, `/file-processing`, `/refunds`, `/contacts`, `/about`, `/status`, `/changelog`, or `/account-linking` page.

### Conversion and activation

- SEO CTAs already send a `prompt` query parameter, but the web app does not yet consume it. Validate length, prefill the composer, and never auto-submit.
- The landing does not show what the free start includes in a stable, production-backed format.
- Model and price facts are duplicated in static SEO code instead of coming from a public, cacheable backend source or verified build-time snapshot.
- There is no guest preview or guided first-value path before authentication.
- The site does not return proof that CTA click, authentication, first completed task, and payment belong to one measurable funnel.

### Technical SEO and measurement

- The canonical origin still falls back to the temporary Lovable hostname. Set production `SITE_URL` once the domain is chosen and verify every canonical, Open Graph URL, sitemap entry, and robots reference.
- The analytics wrapper only pushes to `dataLayer`; no configured consent-aware analytics destination is visible in this repository.
- Google Search Console, Yandex Webmaster, sitemap submission, and Yandex Metrica still need production setup.
- Create a branded 1200x630 share image and verify it on the final domain instead of relying on the current generic asset.
- Add a real favicon/logo set and stable organization identity after the brand decision.
- Monitor index coverage, canonical conflicts, legacy-route cannibalization, Core Web Vitals, CTA handoff success, and conversions by page cluster.

## Recommended implementation sequence

### P0: before buying traffic

1. Consume the SEO prompt handoff in the web app and measure landing -> auth -> first completed task.
2. Add real product screenshots or a short recording; label constructed examples clearly.
3. Replace static pricing and model claims with a public backend feed or a verified build-time snapshot with an update date.
4. Publish reviewed privacy, terms/public offer, refund, file-processing, and contact pages.
5. Configure the final domain, canonical origin, webmaster tools, sitemap submission, consent, and analytics destination.
6. Remove the stale Claude copy and hide or implement Code Interpreter.

### P1: explain the existing workspace advantage

1. Build a focused page for folders, shared folder instructions, conversation search, and personalization.
2. Rework the PDF page around the reusable document library, pinning, attachment, current limits, and real examples.
3. Expand the AI-search page with quick/standard/deep modes, URL reading, finance search, and source-verification examples.
4. Expand help into account, documents, models/tools, billing, cancellation/refunds, data deletion, and troubleshooting sections.
5. Add a reliability section that demonstrates stream recovery and background work without absolute guarantees.

### P2: build an evidence-led content moat

1. Publish role workflows only where the product completes the job: market research, document review, technical research, writing, and study.
2. Run same-task model and image comparisons with published prompts, dates, settings, rubrics, costs, and failure cases.
3. Add permissioned customer cases and quote only measurable outcomes.
4. Publish a maintained changelog and status page.

## Proposed focused routes

| Route direction | Search and product intent | Proof required |
|---|---|---|
| `/rabochie-papki-i-prompty` | Organize AI chats by project and reuse instructions | Folder list, folder prompt, conversation move/search screenshots |
| `/personalizaciya-ai` | Set persistent response preferences | Wizard and resulting prompt, before/after example |
| `/biblioteka-dokumentov` | Reuse pinned documents across chats | Upload, status, pin, attach, supported limits |
| `/ai-poisk-po-ssylke` | Ask AI to read and analyse a specific public URL | Real Sonar request, sources, limitations |
| `/finansovyi-ai-poisk` | Collect public financial information with sources | Real example, timestamp, explicit non-advice disclaimer |
| `/vosstanovlenie-otveta` | Continue work after a short connection interruption | Recorded interruption and successful resume |
| `/upravlenie-podpiskoi` | Understand balance, payment methods, renewal, cancellation, and refund state | Current UI and verified policy |

Do not publish all routes merely to increase page count. Start with the workspace and document-library pages because they expose existing differentiation and support the main research-to-result position.

## Evidence inspected

### Backend

- Conversation and message routes, search, stream resume, and mutations: `app/api/routes.py`, `app/api/chat_helpers.py`.
- Folders and shared prompts: `app/api/chat_folders.py`, `app/api/chat_folder_helpers.py`, `app/api/chat_helpers.py`.
- Personalization: `app/api/personalization.py`, `app/api/personalization_helpers.py`.
- Documents and pinning: `app/api/documents.py`, `app/api/document_helpers.py`.
- Tool construction and provider restrictions: `app/services/subscription_check/realtime_check.py`, `app/services/perplexity_service.py`, `app/services/perplexity_features.py`.
- Models and entitlements: `app/services/model_registry.py`, `app/api/model_catalog_helpers.py`, `app/services/subscription_check/entitlements.py`.
- Billing self-service: `app/api/payments.py`, `app/api/payment_helpers.py`, `app/api/access_code_helpers.py`.

### Frontend

- App flows and stream recovery: `src/pages/Index.tsx`, `src/components/ChatPane.tsx`, `src/lib/streamRecovery.ts`.
- Organization and personalization: `src/components/ConversationSidebar.tsx`, `src/components/FolderSettingsDrawer.tsx`, `src/components/PersonalizationDialog.tsx`.
- Documents, settings, usage, and billing: `src/components/DocumentsManager.tsx`, `src/components/SettingsContent.tsx`, `src/components/SubscriptionDialog.tsx`, `src/components/PaymentMethodsManager.tsx`.
- Public copy and feature flags: `src/lib/i18n.ts`, `src/lib/api.ts`, `src/types/index.ts`.

### SEO repository

- Homepage and information pages: `src/routes/index.tsx`, `src/routes/features.tsx`, `src/routes/models.tsx`, `src/routes/pricing.tsx`, `src/routes/security.tsx`, `src/routes/help.tsx`.
- Long-form product copy: `src/content/pages.ts`, `src/content/blog.ts`.
- Canonical, sitemap, robots, and analytics: `src/lib/seo.ts`, `src/routes/sitemap.xml.ts`, `src/routes/robots.txt.ts`, `src/lib/analytics.ts`.
