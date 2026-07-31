# MicroK8s production edge settings

`nginx-ingress-trusted-proxy-configmap.yaml` allows ingress-nginx to consume
forwarded headers only from the SPB AmneziaWG peer (`10.77.0.2`). Keep this CIDR
narrow: adding public address ranges would allow clients to spoof their source
address.

`ingress-awg-service.yaml` exposes ingress HTTPS on node port `30445` with
`externalTrafficPolicy: Local`. The SPB edge connects to `10.77.0.1:30445`,
and the public application path no longer depends on the controller's host port
`443`. MicroK8s' default ingress controller still applies host-port
masquerading after NodePort routing, so ingress currently records the peer as
loopback. Keep IP-based browser-auth rate limits disabled or conservative until
a dedicated no-hostPort AWG ingress controller is separately approved and
deployed.

```sh
kubectl apply --server-side -f ops/k8s/ingress-awg-service.yaml
```

The Argo CD rollout applications track the branches used by TeamCity:
backend `master` in `mini-app-tg-gpt` and frontend `main` in
`chat-bot-telegram`. Do not point them back to legacy `development` sources;
otherwise self-healing silently removes release-safety changes.

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
