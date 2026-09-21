# UX/UI follow-up review — 21 September 2026

## Result

Implemented in both production-based and beta-integration frontend worktrees. No deployment, push, pricing adjustment, allowance rebalance, or production data change.

## Changes

- Rewrote English and Russian plan cards around actual progression: Start access; Plus 2x capacity with the same models; Premium 5x and Astra/Fable access; Max 20x Start / 4x Premium with Premium's model access. Shared features appear once. Removed repeated generic model explanations and tightened mobile card spacing.
- Preserved AI-preference drafts across desktop settings categories. Closing unsaved preferences now offers keep editing/discard; standalone mobile/desktop preference editors have the same protection. Save is disabled for unchanged/empty content. Associated editor/wizard labels with their controls and removed technical prompt-generation jargon from wizard helper copy.
- Documents now show server-provided retention and per-file expiry dates, readable wrapping filenames, a labelled storage meter, and confirmation before permanent deletion. Removed the one-option provider selector in shared-allowance chat documents. Added explicit close buttons and bounded scrolling for mobile document and preference drawers.
- Project settings now retain edits and display an error when saving fails, disable overlapping saves, and label name/instructions accessibly. Project instructions use a user-facing name. Sheet close labels respect the selected language.

## Actual review coverage

Live isolated preview at http://127.0.0.1:5197/: desktop 1440x1000 and laptop 1280x800, phones 390x844 and 360x800. English/light and Russian/dark. No horizontal page overflow at 360 px in the inspected pricing/usage screens.

Inspected home workflow selection and draft-replacement confirmation; desktop model picker, model details, reasoning/tool/image-quality options; subscription plans and exact-model disclosures; usage model summary, task history and combined filters; settings sections and draft navigation/close recovery; document list/expiry/delete cancellation; sidebar search empty state; project settings; existing image conversation and image viewer; chat actions; Work entry and unavailable state. Keyboard focus and accessible labels were checked in the inspected controls; this is not a complete screen-reader certification.

No new paid model calls were made. No file deletion was executed in the live preview. Project creation made one empty synthetic project named “Новый проект” in the dedicated preview account; no production records were touched.

## Validation

- Feature frontend: 64 test files / 328 tests passed.
- Beta frontend: 69 test files / 355 tests passed.
- TypeScript, production builds, focused ESLint and git diff whitespace checks passed.
- New behavioural regressions cover preference draft preservation/save/cancel, confirmed document deletion, and project save failure/duplicate prevention.
- Existing large-bundle build warnings remain (main bundle about 393–404 KB gzip; syntax-highlighting chunk about 529 KB gzip). These are build measurements, not measured real-user latency.
- No backend application changes in this pass; prior backend checks are documented in the previous report rather than claimed as rerun.

## Remaining before release

1. Review these screens with the user; prices/capacity remain provisional.
2. Work execution was not validated: the local beta integration reports it temporarily unavailable. Verify its intended availability and execution in the target beta configuration, or deliberately scope it out of this release. Do not infer production/beta state from this local preview.
3. Actual iOS/Android keyboard, safe-area, Telegram container and touch behaviour still need real-device smoke checks. Responsive desktop-browser emulation does not prove those.
4. Authentication/passkey enrolment, payments (currently disabled), account deletion, external sharing, uploads and live streaming/provider calls were not re-executed in this UX pass; use the earlier functional evidence plus a target-environment smoke check before deployment.
5. Non-blocking follow-up: project creation currently creates an empty project before its settings are edited; a future create-before-save flow would avoid abandoned empty projects. Mobile Settings remains a long list compared with the desktop categories; friend feedback should determine whether a compact section index is worthwhile. Profile initial-load performance before changing bundle architecture.

English/light restored, viewport override reset, updated plans left open in the review tab. Deployment remains withheld.
