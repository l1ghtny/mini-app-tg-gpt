---
name: AWG ingress client identity chain
description: Safe client-IP preservation from the SPB public edge to browser-auth rate limits
type: ops
---

## Context

Routing the SPB AWG tunnel through MicroK8s' default hostPort ingress made every
request arrive as `127.0.0.1`. Pointing a NodePort at that same controller did
not preserve source identity.

## Production chain

1. SPB Nginx is the public trust boundary. It overwrites `X-Real-IP` and
   `X-Forwarded-For` with `$remote_addr`; it never appends untrusted client
   headers.
2. SPB sends HTTPS over AWG from `10.77.0.2` to `10.77.0.1:30445`.
3. `nginx-ingress-awg-controller` is a dedicated no-hostPort DaemonSet. Its
   NetworkPolicy accepts port 443 only from `10.77.0.2/32`.
4. ingress-nginx trusts forwarded identity only from `10.77.0.2/32` and passes
   the complete chain to backend pods.
5. Backend `WEB_AUTH_TRUSTED_PROXY_CIDRS` must contain exactly
   `10.1.0.0/16,10.77.0.2/32` so `_resolve_client_ip` walks past both trusted
   hops and returns the public client.

## Verification

- Send tagged passkey option requests from two real network origins, each with
  a forged `X-Forwarded-For`.
- The node-2 dedicated-controller access log must show the real origins, not the
  forged values.
- Redis `rl:passkey:ip:<sha256>` keys must exist for the two real origins and
  not for the forged values.
- Direct access to a node public IP on `30445` must time out; SPB direct AWG and
  public `app.lightny.ru` requests must return 200.

## Rollback

Restore the `ingress/nginx-ingress-awg` Service selector to
`name: nginx-ingress-microk8s`. Keep the dedicated controller running while
diagnosing so rollback does not require recreating resources.
