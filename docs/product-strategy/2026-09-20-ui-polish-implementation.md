# Desktop UI refinement implemented — 20 September 2026

Implemented locally on production-based feature and beta-integration worktrees. Nothing deployed or pushed.

- Direct desktop model picker with all seven choices, neighboring conversation controls, consistent names and bilingual task descriptions. Navigation groups are no longer presented as intelligence scores; capability aliases are normalized.
- Desktop Settings has General, AI preferences, Documents, Plan & usage, Account & security and Help navigation. Documents and personalization render within the content panel; mobile drawers remain.
- Balance opens Usage; upgrade actions open Plans. All four plan cards compare side by side when space permits, with 2/1-column fallbacks. Usage summary is constrained to 840px; task history has desktop columns and visible filters, with mobile disclosure/stacking.
- Resizable desktop sidebar (240–420px, persisted locally, keyboard accessible), labeled New chat action, composer aligned to the reading column, secondary chat actions grouped in a menu. Beta Work handoff remains available.
- Image settings lead directly to quality when Flare is the only image model. Tool overrides are disclosed on demand. Project terminology and document action labels improved.
- Shared-cohort document capacities follow the existing monthly allowance grant: Start = legacy Basic (50 docs / 200 MiB / 100 MiB file / 25 pinned); Plus = Advanced (100 / 500 MiB / 250 MiB / 50); Premium and Max = Premium (200 / 1 GiB / 512 MiB / 100). Existing five-day unpinned retention retained. Legacy users outside the cohort unchanged. No plan prices or AI grants rebalanced.

Validation: 40 isolated-schema backend tests passed in each backend worktree; feature frontend full suite 323 tests and beta full suite 350 tests passed, then a new picker-switch regression increased its focused suite to 7 passing tests. TypeScript and both production builds passed; final beta build also passed after accessibility labels and picker-switch fix. Focused lint has no errors (existing hook/fast-refresh warnings). No new paid model calls were needed.

Visual/interaction checks: 1440×1000, 1280×800 and 390×844; EN/RU and light/dark; direct picker, model details, separate controls, image qualities, allowance entry routing, four-card plans, model/task usage, settings navigation, embedded documents and actual 200-doc/1-GiB Premium capacity. Sidebar keyboard resize verified and reset; language/theme restored to EN/light. Browser viewport override reset at handoff.

Beta capability tests use dummy storage configuration for existing Work imports; no storage requests or real credentials are needed.

Next: user review of the local preview at http://127.0.0.1:5197/. Resolve feedback before deciding whether to deploy beta. Deployment remains explicitly withheld. Prices and allowances remain provisional.
