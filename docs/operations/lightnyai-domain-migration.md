# Lightny AI domain compatibility release

Moscow is only an HTTPS reverse proxy. Website, frontend, backend, workers and
existing account data remain in MicroK8s. No new bot or database.

Apply additive migration xv9d0e1f2a3b through backend master before the new API.
It adds nullable browser_origin and rp_id metadata. Legacy passkeys are not
relabeled: a successful cryptographically verified login records their actual RP.
Keep beta on its existing domain/settings. Do not downgrade shared schema for rollback.

Production configuration (preserve all other existing values):
- WEB_AUTH_ADDITIONAL_ORIGINS=https://app.lightny.ru,https://app.lightnyai.ru
- Append https://app.lightnyai.ru to CORS_ALLOWED_ORIGINS and PASSKEY_ALLOWED_ORIGINS.
- Preserve WEBAPP_URL and TELEGRAM_OIDC_REDIRECT_URI until coordinated entry-link switch.
- Keep host-only Secure session cookies. Register both Telegram callbacks/origins.

New app uses /api and /images on its current origin. Streaming, admin requests,
exports, installation, legal/help and share links follow that origin. Known public
image URLs map to the /images prefix, with canonical URLs retained for the authenticated
backend proxy. Moscow removes cookies and Authorization before the public image relay.

What's New: required at public domain cutover because login, passkey enrollment and
Home Screen installation change. Do not insert a draft into the shared production/beta
feed. The compatibility release preserves existing entry points. Publish one verified
bilingual announcement at launch, with instructions to sign in using the existing
Telegram account, check history, enroll a new passkey, and install the new Home Screen
app before removing the old icon. Never carry session tokens in migration URLs.

Acceptance still required after deployment: old/new Telegram login to the same account,
new passkey enrollment/login, real chat/stream/resume, uploads/images/downloads,
payment notification/return paths, and Safari/Telegram iOS with fraud protection on.
Public bot links remain the user's final cutover action.
