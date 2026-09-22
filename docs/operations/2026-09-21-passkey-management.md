# Passkey management — 21 September 2026

Implemented locally in paired `codex/passkey-management-20260921` worktrees:

- Backend: `/private/tmp/lightny-passkey-management/backend`, based on d414100.
- Frontend: `/private/tmp/lightny-passkey-management/frontend`, based on e604801.

Release to production and beta was authorized on 22 September, after the usage-efficiency release completes. Local validation is complete; release evidence will be recorded below.

## User-visible changes

Sign out now appears in Account & security → Manage beside Sign out other devices, instead of Help. Mobile uses the same management grouping. Logout behavior and Telegram visibility are unchanged. This placement follow-up passed ESLint, TypeScript, diff checks and desktop/mobile visual review.

Legacy default names `Passkey` and `Ключ доступа` follow the current UI language. Custom names remain untouched. Registration no longer sends a translated default for persistence. Users can rename keys, including old ones.

The list and deletion confirmation show the stored site association, creation timestamp, last successful passkey sign-in, and browser/OS context when available. Unknown historical metadata stays unknown. Existing RP resolution and migration behavior are unchanged.

Browser/OS values describe the browser used for registration or successful sign-in, not the physical authenticator or password manager. Cross-device authentication, syncing and user-agent reduction prevent reliable device ownership claims. Store only coarse allowlisted browser/OS names, with no raw user-agent, IP, device fingerprint or hardware identifier. No new attestation requirement.

The delete confirmation identifies the chosen key and explains that its synchronized copies will no longer authenticate, while the password-manager entry may remain. Cancel is initially focused; errors preserve the dialog and selected key for retry. Last-login-method rules and credential verification are unchanged.

## API and release contract

- Additive nullable columns: `created_browser`, `created_os`, `last_used_browser`, `last_used_os`.
- Migration `xw0e1f2a3b4c` follows `xv9d0e1f2a3b`; reapplying its upgrade is safe. No historical device or RP values are guessed.
- Passkey list/registration responses add those fields and expose the existing `rp_id`.
- Authenticated `PATCH /api/v1/auth/passkeys/{id}` renames an owned key, limits names to 80 characters and rejects blank names. Foreign keys return 404. Existing cookie-origin protection applies.
- Frontend files: `src/lib/api.ts`, `src/components/SettingsPanel.tsx`, `src/components/PasskeyRow.tsx`, `src/lib/i18n.ts`.
- Release order: shared-schema migration, backend, frontend. Merge the required schema ancestry into beta before deploying it there. Do not run the repository-wide database-reset fixture against a shared database.

## Validation and review

- 41 isolated backend cases cover display context, successful registration/authentication metadata, rename ownership/validation, legacy serialization, additive/idempotent migration, and existing passkey/origin/session safeguards. Database dependencies are mocked; migration behavior is exercised on a disposable SQLite database.
- 13 frontend cases cover localization, custom-name preservation, rename retry, delete confirmation/cancel/failure/retry, existing-key registration feedback and settings navigation.
- TypeScript, focused ESLint/Ruff and production build pass. Existing bundle-size/dynamic-import warnings remain.
- Actual settings and dialogs reviewed at 1440 and 390 px, RU/dark and EN/light. Synthetic rename updated the selected key; synthetic deletion left the other key intact.
- Local synthetic preview: `http://127.0.0.1:5199/__passkey-review.html`. Open Account & security on desktop. This does not access real account keys. Fixture files `__passkey-review.html` and `src/__passkey-review.tsx` must stay out of release commits.

Next: wait for the usage-efficiency release, integrate both branch pairs, release production (including the additive shared-schema migration), then beta, and verify CI plus running versions. Real passkey registration/sign-in acceptance remains a separate user-device check. Newly recorded metadata only becomes available after release; existing historical devices cannot be reconstructed.
