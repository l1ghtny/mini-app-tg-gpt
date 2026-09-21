# Chat and allowance UI review before beta

Reviewed the local beta-integration preview at 1440×1000 and 390×844 on 20 September 2026. This is a review, not a redesign implementation or a deployment sign-off.

## Overall assessment

The main chat reading surface is usable. The strongest desktop problem is the layout of secondary surfaces: Usage expands a narrow three-column list across a 1152px dialog, Settings uses a 560px vertical mobile-style menu, and the model picker squeezes seven models into a 420px popover with a 512px height cap. Desktop space is available but does not consistently make comparison or navigation easier.

Keep the established typography, blue accent, restrained surfaces and existing mobile drawers. Introduce desktop-specific arrangements at the component level. Globally shrinking type, touch targets or corner radii would not solve the main problems.

## Fix before beta feedback

### 1. Model details contain misleading and untranslated information

Observed Sonnet's info dialog showing “Intelligence: 1/5”, `explainer.cap_image_gen` and `explainer.cap_thinking`.

`app/services/allowance.py:catalog()` sends the four navigation-group ranks (0–3) as `intelligence`. `src/components/ModelExplainer.tsx` renders that number out of five. It also renders each enabled capability directly through its translation key, but the new catalog uses `image_gen` and `thinking`, which are absent from the explainer translations.

Remove the invented intelligence score rather than changing its scale. Normalize capabilities to the supported UI vocabulary and avoid duplicate thinking/reasoning badges. Give each model a short, meaningful task description. Flare's info sheet currently adds only low/medium/high labels, repeating controls already available in settings; either make the details useful or remove that redundant disclosure.

Acceptance: all seven text models and Flare have readable EN/RU information, no raw keys, no group number presented as a benchmark, and consistent display names across picker, usage and plans.

### 2. Open the destination implied by the entry point

The header's “87%” allowance control opens Plans by default. The shared subscription component ignores the existing initial-tab routing and uses `Tabs defaultValue="plans"`.

Clicking the allowance balance should open Usage. Upgrade actions should open Plans. On desktop, the balance can visibly say “87% left”; the accessible label already explains that it is remaining allowance.

Acceptance: balance, upgrade and account-management entry points open the intended view. Returning from details preserves context where practical.

### 3. Make model selection a direct desktop action

The desktop flow is model chip → conversation settings → choose a model → scrolling. In the reviewed 1440px viewport, the initial list showed Everyday and Standard; Advanced and Flagship required scrolling. Repeated “Shared AI allowance” lines, scope explanation and navigation consume substantial picker height.

Separate model selection from the broader conversation controls on desktop. A compact picker should show all seven choices together at typical laptop heights, retaining selected/locked states and the four helpful groups. Put reasoning, tool overrides and image quality in a neighboring controls surface. Keep automatic tools as the normal path and disclose tool-forcing options when requested. With Flare as the sole image model, prioritize quality selection over another one-option model picker.

Acceptance: one click opens model choices; every model is discoverable without navigating another settings page; short model descriptions help selection; keyboard and touch interaction remain usable.

### 4. Check document entitlements on the actual beta cohort

The local test user displays Premium · 5× Start but Documents shows a two-file limit, 5 MB maximum file size, 10 MB storage and zero pinned files. This is not sufficient evidence of a production defect: the preview explicitly grants a Premium allowance while its synthetic user's legacy subscription is free. However, `app/api/document_helpers.py:get_document_capabilities()` still resolves document quotas from the legacy active tier, independently of the allowance plan.

Choose and verify intended beta document capacity for the friend/private users. If these systems intentionally remain separate during beta, the presentation must not imply a document upgrade that was not granted. Keep the actual file-size limits visible in the upload flow.

## Recommended desktop polish

### Usage and history

Keep the new Chat models / Images distinction and the exact-once cost attribution. Constrain the overview's table width (roughly 760–900px, tuned in-browser) or use the remaining desktop width for selected-model details. The current model names and figures are separated by a very large empty gap. Use a compact fixed header and let the content area scroll, so navigation does not disappear into a tall dialog.

Detailed history should use desktop columns for task, chat/project, time and allowance. Keep the existing stacked rows on mobile. Show the relevant filters in a compact desktop toolbar; a Filters disclosure remains appropriate on small screens. Keep loading, pending, failed and included states distinct.

Do not introduce extra dashboards, charts or decorative summary cards merely to fill the space.

### Plans

The current cards are a substantial improvement over comma-separated model names. Preserve capability-first copy and expandable model details. At wide desktop sizes, display all four tiers in comparable columns; Max is currently hidden below the three main cards even when ample width is available. Use two columns on intermediate screens, then stack on phones.

Compare consistent attributes: price, monthly allowance multiple, model access and intended task intensity. State common benefits once. Keep the distinction between more allowance and access to flagship models explicit. Avoid describing a capacity multiple as an equivalent improvement in answer quality. Purchases being disabled is intentional for this preview, not a checkout bug.

### Settings, personalization and documents

The desktop Settings dialog is a long 560px column. Personalization and Documents appear below passkeys, appearance and app installation, making the product's important capabilities harder to discover. Documents opens another modal over Settings, leaving two layers of dimmed panels.

Use desktop category navigation such as General, AI preferences, Documents, Plan & usage, and Account & security, with one content panel. On mobile retain familiar stacked navigation. Promote personalization and document access to recognizable destinations; preserve provider-destination disclosure near upload, without presenting “Upload as OpenAI” as a choice when only one destination exists.

“Main user prompt” and “Run setup wizard” make personalization sound like configuration machinery. Prefer direct questions about how the assistant should respond, with advanced instructions available for users who want them. Keep privacy/account actions accessible in their relevant category.

### Main chat and sidebar

The text conversation is readable and has a reasonable bounded reading column. Avoid making long prose span the entire desktop window. Align the composer and conversation widths more consistently; the composer currently extends substantially beyond the text column. Make model and response controls easier to notice within it.

The sidebar's approximately 270px width truncates long titles very early. A resizable desktop sidebar would help frequent users. A clear “New chat” label could replace the main icon-only plus action; secondary export/share/Work actions can stay in a compact overflow menu when appropriate. Use one term consistently: the sidebar says projects while usage filters say folders. The history-management dialog currently focuses on deletion, so its label should communicate that scope.

The repeated near-identical conversation titles in this fixture are mostly generated test data; they are not evidence that real-user auto-titling is broken.

## Suggested sequence

1. Correct model metadata/translations and shared-allowance entry routing; verify actual beta document grants.
2. Improve the desktop model picker and usage overview/detail layout.
3. Give Settings desktop category navigation and make all four tiers comparable at wide widths.
4. Recheck 390px, ordinary laptop and large desktop widths; verify EN/RU, keyboard/focus, long titles, large text and relevant loading/empty/error states.

Resizable sidebar and finer visual polish can follow beta feedback. No need to hold a private feedback release for a comprehensive visual redesign after the correctness issues and primary desktop friction are addressed.

## Scope and limits of this review

Visually inspected local chat with text and generated images, existing empty chat, model selection and model info, image quality options, settings, personalization, document library, history-management navigation, plans and usage. Checked desktop and mobile layouts and traced the relevant source paths.

Did not submit messages, generate images, modify preferences, upload/delete files, change account security or execute payments. Did not re-test actual provider execution, beta deployment, production authentication, dark theme or an end-to-end purchase flow in this review. Work is outside the current basic-chat scope. Populated project/folder rendering was not established from this synthetic account. No application code changed.
