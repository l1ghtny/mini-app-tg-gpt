---
name: lightny.ru SPB AmneziaWG endpoint on k8s-node-2
description: lightny.ru reaches the GPT mini app through SPB nginx and an AmneziaWG tunnel terminated on k8s-node-2.
type: ops
---

## Context

`https://lightny.ru` is the Telegram mini app URL for Russian users. It is served by the SPB server nginx and proxied through AmneziaWG to the Kubernetes cluster.

On 2026-07-04, `main-server` was healthy for Kubernetes but unreachable from the SPB server at the network layer. SPB could reach `k8s-node-2`, so the AmneziaWG cluster endpoint was moved there.

## Current Tunnel Layout

- SPB server public IP: `193.233.251.127`
- SPB AmneziaWG interface: `awg0`, `10.77.0.2/24`, listen port `51820`
- Cluster endpoint: `k8s-node-2`, public IP `152.53.33.181`
- Cluster AmneziaWG interface: `awg0`, `10.77.0.1/24`, listen port `41932`
- Node2 AmneziaWG public key: `0utAbOK0AFbrBqIozNoHlbckyHG3Qou4Ce+yJRnLiX0=`
- SPB AmneziaWG public key: `tmrH2jfSCv8me8beLpxiXUIypYofuNTdb3R7bZJaInU=`
- Old `main-server` `awg-quick@awg0` service is disabled/inactive; keep its config on disk only as rollback reference.

## Image Proxy

The old `main-server` Docker container `r2-proxy` bound `10.77.0.1:8443` and TCP-proxied to `tg-bot-images.lightny.pro:443`.

After moving `10.77.0.1` to `k8s-node-2`, the replacement is the Kubernetes manifest:

- `k8s/tg-bot-images-tcp-proxy-node2.yaml`

It deploys `nginx:alpine` in namespace `gpt`, pinned to `k8s-node-2`, using `hostNetwork` and listening only on `10.77.0.1:8443`.

## Gotchas

- The SPB root environment has proxy variables pointing to `socks5://127.0.0.1:1080`. Use `env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy curl ...` for diagnostics.
- `curl -I /api/...` may return `405` for endpoints that require `GET`; that still proves proxy reachability.
- Do not route `lightny.ru` around the tunnel as a first fix. The intended production path is SPB nginx over AmneziaWG.
- On 2026-07-08, intermittent app failures were traced to SPB nginx connection exhaustion, not AWG packet loss. `/lightny-connect` WebSocket traffic shares the same `lightny.ru` nginx worker pool as the mini app and can consume roughly two file descriptors per upgraded connection.
- SPB nginx was tuned from `worker_connections 768` and soft nofile `1024` to `worker_rlimit_nofile 65535`, `worker_connections 8192`, `multi_accept on`, and `worker_shutdown_timeout 30s`; backup: `/etc/nginx/nginx.conf.codex-backup-20260708-173259`.
- If this recurs, check `/var/log/nginx/error.log` for `worker_connections are not enough`, count `ss -Htan state established dport = :10001`, and consider isolating `/lightny-connect` onto a separate hostname/nginx instance so VPN/WebSocket load cannot starve the Telegram mini app.
