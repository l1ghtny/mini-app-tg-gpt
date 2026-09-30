# Beta on the Moscow edge

Public URL: `https://beta.app.lightnyai.ru`. DNS A record points to
`193.233.244.246`, TTL 60. Beta keeps its existing user allowlist, database,
Redis isolation, and disabled billing actions. Claude remains paused.

`k8s/beta/origin-tls.yaml` is provisioned by infrastructure operations before deployment;
CI retains its existing permissions and applies only the Ingress.
`k8s/beta/origin.yaml` creates `lightnyai-beta.internal` on `public-awg`.
Its private origin certificate renews through the existing cert-manager CA.
Nginx verifies that CA; the public certificate renews through the edge's
existing Certbot IPv4 timer and Nginx reload hook. The image relay shares the
existing guarded image route and strips Authorization/Cookie.

`nginx.conf` is installed as `/etc/nginx/sites-available/lightnyai-beta`, with
the enabled link named `lightnyai-z-beta` so shared production log/map
definitions load first. API writes have retries disabled; SSE buffering stays
off. Both readiness URL forms route to FastAPI JSON, never the frontend SPA.

`route_guard.py` installs as `/opt/lightnyai/bin/beta-route-guard`. Its separate
systemd timer checks beta frontend JSON and backend/database/Redis through all
three existing tunnels every ten seconds. Failed peers leave routing; returning
peers need three successful checks. It uses the common Nginx configuration lock,
separate state and backups, and cannot parse or change production upstreams.

For an authorized reinstall, run `python3 ops/beta-edge/install.py bootstrap`,
issue the public certificate using the existing edge Certbot wrapper, verify
private origins, then run `python3 ops/beta-edge/install.py activate`.
The installer backs up prior beta files, validates Nginx, and rolls back routing
on validation/reload failure. Run `python3 ops/beta-edge/test_route_guard.py`.

The beta CI pipeline builds frontend API/web URLs for the new domain and uses
the new public readiness check. Preserve live Sentry build/source-map arguments
when updating the pipeline. Secret URL settings must match the defaults in
`scripts/release/create_beta_secrets.sh`; changing them never requires replacing
the existing signing key or provider credentials. Beta-only release: production
versions remain 2.0.1 and no shared What's New row is written.

Browser sessions and passkeys are domain-bound. New-domain users sign in again;
old-domain passkeys require registration on the new domain. Telegram must also
allow the new callback in its bot's Web Login allowed URLs.
