# MicroK8s production edge settings

`nginx-ingress-awg-controller.yaml` runs a dedicated controller without
`hostNetwork` or `hostPort`. It watches only the `gpt` namespace and mirrors the
existing `public` ingress class, so Argo Rollouts continues to own the stable and
canary Ingress objects.

`nginx-ingress-trusted-proxy-configmap.yaml` allows that controller to consume
forwarded headers only from the SPB AmneziaWG peer (`10.77.0.2/32`). Keep this
CIDR exact: adding public address ranges would allow direct clients to spoof
their source address.

`ingress-awg-service.yaml` exposes the dedicated controller on node port
`30445` with `externalTrafficPolicy: Local`. The SPB edge connects to
`10.77.0.1:30445`. Rollback only requires restoring that service's selector to
`name: nginx-ingress-microk8s`; the default hostPort controller remains running.
The controller manifest also restricts HTTPS ingress to `10.77.0.2/32`, so the
NodePort cannot be used through a cluster node's public address. Health port
`10254` remains reachable for Kubernetes probes.

The SPB Nginx locations include `ops/nginx/lightny-proxy-headers.conf`, which
overwrites client-supplied forwarding headers with `$remote_addr`. Do not use
`$proxy_add_x_forwarded_for` at this public trust boundary.

```sh
kubectl apply --server-side -f ops/k8s/nginx-ingress-trusted-proxy-configmap.yaml
kubectl apply --server-side -f ops/k8s/nginx-ingress-awg-controller.yaml
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
- `WEB_AUTH_TRUSTED_PROXY_CIDRS=10.1.0.0/16,10.77.0.2/32`
- `R2_PUBLIC_BASE_URL=https://app.lightny.ru/images/`

Email and Telegram OIDC login must remain hidden in the frontend until their
backend credentials are configured. Passkey enrollment is bootstrapped from an
authenticated Telegram Mini App session.
