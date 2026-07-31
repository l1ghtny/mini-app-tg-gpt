# MicroK8s production edge settings

`nginx-ingress-trusted-proxy-configmap.yaml` allows ingress-nginx to consume
forwarded headers only from the SPB AmneziaWG peer (`10.77.0.2`). Keep this CIDR
narrow: adding public address ranges would allow clients to spoof their source
address.

The current edge path uses the controller's host port `443`. MicroK8s rewrites
that connection to loopback, so ingress cannot safely recover the public client
address from `X-Forwarded-For`. Keep IP-based browser-auth rate limits disabled
or conservative until a separately approved source-IP-preserving service is
deployed.

The production `backend-env` Secret also needs these non-secret values:

- `WEBAPP_URL=https://app.lightny.ru`
- `WEB_AUTH_ENABLED=true`
- `TELEGRAM_OIDC_ENABLED=false`
- `PASSKEY_RP_ID=app.lightny.ru`
- `PASSKEY_RP_NAME=AIwithUI`
- `PASSKEY_ALLOWED_ORIGINS=https://app.lightny.ru`
- `AUTH_COOKIE_SECURE=true`
- `AUTH_COOKIE_SAMESITE=lax`
- `WEB_AUTH_TRUSTED_PROXY_CIDRS=10.1.0.0/16`
- `R2_PUBLIC_BASE_URL=https://app.lightny.ru/images/`

Email and Telegram OIDC login must remain hidden in the frontend until their
backend credentials are configured. Passkey enrollment is bootstrapped from an
authenticated Telegram Mini App session.
