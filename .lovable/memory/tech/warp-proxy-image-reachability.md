---
name: WARP proxy and public image reachability
description: Keep Lightny image checks independent of WARP and probe real SOCKS egress before routing traffic.
type: ops
---

## Context

Chat creation builds image-bearing history before returning `202`. Public image
reachability checks inherited `HTTP_PROXY` and `HTTPS_PROXY`, so an unhealthy
WARP endpoint could stall each image check even though Redis, the API, and the
SOCKS listener itself were healthy.

## Decision

- Lightny-owned public image reachability checks use direct networking with
  `httpx.AsyncClient(trust_env=False)`.
- WARP startup, readiness, and liveness probes must perform an HTTPS request to
  `tg-bot-images.lightny.pro` through `127.0.0.1:1080` with remote DNS.
- A TCP-only probe is insufficient because the failed pod continued accepting
  SOCKS connections while its WARP tunnel could not complete them.

## Constraints

- Keep general provider traffic on WARP where configured; the direct-network
  exception is scoped to Lightny-owned image storage.
- Validate the probe command against the exact deployed WARP image before
  changing it; it currently depends on `/usr/bin/curl`.
- Treat stale request-ledger reconciliation as a separate audited database
  operation. Do not infer successful billing or completion from a reserved row.
