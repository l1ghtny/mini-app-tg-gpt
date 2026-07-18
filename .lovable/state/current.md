# Current State

## Current objective

Balance memory-heavy Kubernetes workloads onto `new-node` with worker failover.

## In progress

- None.

## Completed

- Labeled `new-node` with `workload.cybercolors.dev/high-memory=true`.
- Added soft high-memory preference and `main-server` exclusion to the conversation-search worker and WARP proxy manifests.
- Live placement after rollout:
  - conversation-search worker on `new-node`
  - one WARP replica on `new-node`
  - one WARP replica on `k8s-node-3`
- Scheduling remains failover-capable because `new-node` is preferred, not required; node 2 and node 3 remain eligible.
- Verified both deployments rolled out successfully and the public GPT API remained HTTP 200.
- Inspected `gamedev` TeamCity Kubernetes objects:
  - existing `teamcity-agent-deployment` is a single-replica Deployment pinned to `main-server`
  - existing agent uses `hostPath` directories under `/opt/buildagent/...`, not PVCs
  - `teamcity-data-pvc` and `teamcity-logs-pvc` are mounted by `teamcity-server-deployment`
  - current agent uses fixed `AGENT_NAME=gamedev-teamcity-agent`, so scaling it directly would duplicate identity
- Added `k8s/teamcity-extra-agents.yaml` with two one-replica Deployments:
  - `teamcity-agent-k8s-node-2`
  - `teamcity-agent-k8s-node-3`
  - each has a dedicated 30Gi `microk8s-hostpath` PVC and stable unique `AGENT_NAME`
  - first version mounted `/var/run/docker.sock` from each node and persisted TeamCity conf/work/temp/tools/plugins/system/logs on PVC
- Added durable memory note `.lovable/memory/tech/teamcity-agent-scaling.md`.
- Applied the first manifest; both 30Gi PVCs bound on `k8s-node-2` and `k8s-node-3`.
- Verified both first-version pods are pending because the worker nodes do not expose `/var/run/docker.sock`.
- Applied the approved PVC-backed privileged `docker:29-dind` sidecar manifest.
- Verified both new pods are `2/2 Running` on `k8s-node-2` and `k8s-node-3`.
- Verified both agent containers can reach Docker `29.6.1` through their sidecars.
- Verified TeamCity registered the new agents as ids `311` and `312`; both are currently unauthorized.
- Observed first-run TeamCity plugin sync downloading into the persistent PVC-backed agent directories.
- Bumped `app/core/version.py` from `1.6.2` to `1.6.3` for the release.
- Investigated Sentry issue `82950975` / `GPT-MINI-APP-BACKEND-P`:
  - endpoint: `POST /api/v1/conversations/{conversation_id}/messages`
  - error: OpenAI `BadRequestError` for invalid `history_summary` JSON schema
  - root cause: `SUMMARY_JSON_SCHEMA` declared `open_tasks`, `constraints`, and `decisions` in `properties` but omitted them from `required` while `strict: True` is used
- Fixed `app/services/openai_service.py` so the strict summary schema requires every declared property.
- Added `tests/test_openai_service_schemas.py` to lock strict-schema requirements for title and summary schemas.
- Added production WhatsNew entry for the mobile text selection release:
  - id: `2026-07-01-mobile-text-selection`
  - kind: `improvement`
  - published_at: `2026-07-01T13:06:00`
  - title_en: `Mobile text selection is easier`
  - title_ru: `Выделять текст на телефоне стало проще`
  - inserted directly into production `whats_new_item` via the backend pod using Unicode escapes for Russian text
- Added production WhatsNew announcement directly to `whats_new_item`:
  - id: `2026-06-25-perplexity-ai-search`
  - kind: `feature`
  - CTA: `open_settings`
  - published_at: `2026-06-25T12:40:13`
  - note: first upsert succeeded but PowerShell mangled Cyrillic; immediately corrected `title_ru`, `body_ru`, and `cta_label_ru` using Unicode escapes and verified stored codepoints
- Fixed migration failure reported during `alembic upgrade head`:
  - root cause: migration used `ai_model_pricing`, but the existing SQLModel table is named `aimodelpricing`
  - patched `l1a2b3c4d5e6_add_perplexity_sonar_models.py` to seed/delete from `aimodelpricing`
- Added Perplexity config:
  - `PERPLEXITY_API_KEY`
  - `PERPLEXITY_API_BASE_URL`
  - `PERPLEXITY_SEARCH_CONTEXT_SIZE`
- Added `sonar` and `sonar-pro` to the backend text model registry under provider `perplexity`.
- Added `app/services/perplexity_service.py` to call Perplexity Sonar through OpenAI-compatible chat completions and normalize stream output into existing SSE event types.
- Wired `stream_normalized_ai_response()` to route Perplexity models to the new provider adapter.
- Kept Perplexity text-only:
  - rejects image input
  - rejects image generation
  - keeps file search restricted to OpenAI
  - uses the existing OpenAI image default for conversation/settings compatibility
- Added migration `l1a2b3c4d5e6_add_perplexity_sonar_models.py` to seed:
  - text model catalog rows
  - provider pricing rows
  - tier limits
  - usage pack limits where matching source limits exist
- Added focused tests in `tests/test_perplexity_provider.py`.
- Added durable memory note `.lovable/memory/tech/perplexity-sonar-provider.md`.
- Added Perplexity request controls:
  - `search_mode`: `quick`, `standard`, `deep`
  - `tool_choice`: `fetch_url`
  - `tool_choice`: `finance_search`
- Routed normal Perplexity AI Search through Sonar chat completions and mapped `search_mode` to `web_search_options.search_context_size`.
- Routed explicit `fetch_url` and `finance_search` tool choices through Perplexity Agent API.
- Normalized Agent API output into the same SSE event shape used by the rest of the app.
- Appended Agent API source URLs to assistant text.
- Stored Perplexity Agent API exact usage costs in existing token usage cost columns when `usage.cost` is returned.
- Kept `fetch_url` and `finance_search` scoped to Perplexity models through backend validation.
- Updated `.lovable/memory/tech/perplexity-sonar-provider.md` with the new tool/search-mode contract.

## Verification

- `poetry run pytest tests\test_openai_service_schemas.py tests\test_history_building.py tests\test_perplexity_provider.py tests\test_models_catalog_endpoint.py tests\test_tier_daily_display.py` passed: 22 tests, 1 warning from `google.genai.types`.
- `poetry run pytest tests\test_openai_service_schemas.py` passed: 2 tests.
- `poetry run pytest tests\test_history_building.py` passed: 7 tests, 1 warning from `google.genai.types`.
- `poetry run alembic upgrade head`
  - passed locally after the pricing table-name fix
- `poetry run pytest tests/test_perplexity_provider.py tests/test_google_provider_contracts.py tests/test_user_settings_endpoint.py tests/test_models_catalog_endpoint.py`
  - passed: 10 tests
- `poetry run pytest tests/test_perplexity_provider.py tests/test_models_catalog_endpoint.py tests/test_text_daily_reset.py tests/test_text_infinite_entitlement.py`
  - passed: 10 tests after the migration fix
- `poetry run pytest tests/test_text_daily_reset.py tests/test_text_infinite_entitlement.py`
  - passed: 5 tests
- `poetry run python -m py_compile app\services\perplexity_service.py app\services\ai_service.py app\services\model_registry.py app\api\chat_helpers.py app\api\model_catalog_helpers.py app\services\subscription_check\realtime_check.py migrations\versions\l1a2b3c4d5e6_add_perplexity_sonar_models.py`
  - passed
- `poetry run alembic heads`
  - current head: `l1a2b3c4d5e6`
- `poetry run python -m py_compile app\services\perplexity_service.py app\services\ai_service.py app\api\chat_helpers.py app\api\helpers.py app\schemas\chat.py`
  - passed
- `poetry run pytest tests\test_perplexity_provider.py`
  - passed: 7 tests
- `poetry run pytest tests\test_perplexity_provider.py tests\test_google_provider_contracts.py tests\test_user_settings_endpoint.py tests\test_models_catalog_endpoint.py`
  - passed: 13 tests

## Blockers and risks

- TeamCity reports `gamedev-teamcity-agent-k8s-node-2` and `gamedev-teamcity-agent-k8s-node-3` as unauthorized, so they cannot run builds until approved in TeamCity.
- First-run plugin sync is still in progress; this is expected for fresh persistent agent directories and should settle before first build use.
- The new agents use privileged Docker-in-Docker sidecars and expose the Docker API on `localhost:2375` inside each pod.

## Next steps

- Monitor worker memory and application health after redistribution.
- Keep Prometheus on node 2 until its `emptyDir` TSDB has a deliberate persistence/migration plan.
- Authorize TeamCity agents `311` and `312` if still pending.

## Latest update

- Checked the 2026-07-08 Telegram mini app reliability report against the SPB -> AmneziaWG -> k8s-node-2 path:
  - public `https://lightny.ru/` and `https://lightny.ru/api/v1/models/catalog` returned HTTP 200 during verification
  - SPB `awg0` is active as `10.77.0.2/24`; node2 endpoint is `152.53.33.181:41932`; latest handshake was recent and keepalive remains every 25s
  - SPB ping to node2 tunnel IP `10.77.0.1` was stable at ~33ms with 0% loss
  - SPB direct upstream probes through the tunnel to `gpt-mini-app.lightny.pro` and `gpt-mini-app-api.lightny.pro` returned HTTP 200 quickly
  - MicroK8s nodes and GPT backend/frontend rollouts are Healthy
- Found the real stability issue on SPB nginx:
  - `/var/log/nginx/error.log` had `768 worker_connections are not enough` alerts, including `2026-07-08 17:18:17 MSK`
  - the alerts were caused by bursts of `/lightny-connect` upgraded WebSocket connections sharing the same `lightny.ru` nginx worker pool as the mini app
  - live counts showed hundreds of established TLS connections and hundreds of local upstream connections to `127.0.0.1:10001`, enough to exhaust `worker_connections 768`
- Applied SPB nginx capacity tuning:
  - backup: `/etc/nginx/nginx.conf.codex-backup-20260708-173259`
  - added `worker_rlimit_nofile 65535`
  - added `worker_shutdown_timeout 30s`
  - raised `worker_connections` from `768` to `8192`
  - enabled `multi_accept on`
  - `nginx -t` passed and `systemctl reload nginx` succeeded
  - new nginx worker has `Max open files 65535`
  - public `lightny.ru` root/API and SPB-to-cluster tunnel probes stayed HTTP 200 after reload
- Updated `.lovable/memory/tech/lightny-spb-amneziawg-node2.md` with the nginx connection-exhaustion finding and mitigation.

- Fixed the 2026-07-04 `https://lightny.ru` Telegram mini app outage without abandoning the AmneziaWG tunnel:
  - root cause: SPB could not reach `main-server` (`152.53.95.40`) at the network layer, including UDP/TCP/ping, while `k8s-node-2` (`152.53.33.181`) was reachable
  - installed AmneziaWG packages/DKMS/tools on `k8s-node-2`
  - created a fresh node2 tunnel key and configured `k8s-node-2` as `10.77.0.1/24` on `awg0`, listening on UDP `41932`
  - updated SPB `/etc/amnezia/amneziawg/awg0.conf` to peer with node2 public key `0utAbOK0AFbrBqIozNoHlbckyHG3Qou4Ce+yJRnLiX0=` at `152.53.33.181:41932`
  - verified both SPB and node2 show recent AmneziaWG handshakes and traffic
  - stopped and disabled the old `main-server` `awg-quick@awg0` service to avoid duplicate tunnel ownership
  - added and applied `k8s/tg-bot-images-tcp-proxy-node2.yaml` so `/images/` keeps its `10.77.0.1:8443` TCP proxy after the tunnel endpoint move
  - verified public `https://lightny.ru/` returns 200, current JS/CSS assets return 200, and `https://lightny.ru/api/v1/models/catalog` returns the backend catalog
  - verified `/images/` no longer hangs; empty root path returns normal upstream 404/403 responses
  - cleaned up temporary `node-debugger-*` pods in the `default` namespace
  - added durable ops note `.lovable/memory/tech/lightny-spb-amneziawg-node2.md`

- Updated production `text_model_catalog` copy for Perplexity so subscription/usage surfaces describe it as AI Search:
  - `sonar`: `AI Search with sources` / `??-????? ? ???????????`
  - `sonar-pro`: `Deep AI Search` / `???????? ??-?????`
- Corrected the production Cyrillic update using Unicode escapes after raw PowerShell piping mangled Cyrillic output.
- Updated local `model_registry.py` fallback names and the Perplexity seed migration copy to match prod.
- Checked current mini app outage report on 2026-07-04:
  - Kubernetes cluster is healthy: `main-server`, `k8s-node-2`, and `k8s-node-3` are Ready.
  - Backend rollout `tg-mini-backend` is Healthy on image `localhost:32000/tg-mini-app-backend:254`.
  - Frontend rollout `tg-mini-frontend` is Healthy on image `localhost:32000/tg-mini-frontend-new:112`.
  - Public Cloudflare path `https://gpt-mini-app.lightny.pro/` returns 200 and serves current JS/CSS assets.
  - `https://lightny.ru/` resolves to `193.233.251.127`, establishes TCP/TLS, then returns no HTTP bytes until timeout.
  - Timed-out `lightny.ru` requests do not appear in Kubernetes ingress logs, so the immediate failure is before cluster ingress, likely on the SPB reverse proxy or its Amnezia/WireGuard route to the cluster.
  - Direct cluster ingress with Host `gpt-mini-app.lightny.pro` returns 200 on all three node IPs; direct Host `lightny.ru` returns 404 because Kubernetes has no `lightny.ru` ingress host.

## 2026-07-07 Sentry GPT-MINI-APP-BACKEND-5G

- `GPT-MINI-APP-BACKEND-5G` is a live DB DNS/service-discovery failure on `GET /api/v1/models/catalog`, not an OpenAI/Perplexity application bug.
- Sentry stack shows `asyncpg` failing to resolve/connect to `tg-mini-app-db-primary.gpt.svc:5432`.
- A public endpoint probe created a fresh event at `2026-07-07T11:37:14Z`; related issues `5F`, `5H`, `5J`, `5K`, and `5M` are DB DNS/timeouts in the same window.
- Kubernetes `/readyz?verbose` reported `etcd failed`, `etcd-readiness failed`, and informer/controller failures; subsequent `kubectl get` calls timed out.
- User started rebooting `main-server`; next checks are API readiness, DB service DNS, backend catalog endpoint health, and Sentry silence after reboot.

## 2026-07-07 main-server pressure relief

- Scaled AFFiNE down to stop the Prisma migration loop:
  - `affine/deploy affine` -> 0 replicas
  - `affine/deploy redis` -> 0 replicas
  - `affine/statefulset postgres` -> 0 replicas
  - force-deleted stuck AFFiNE pod objects after they remained Terminating
- Scaled `gamedev/deploy teamcity-agent-deployment` to 0 replicas to stop the legacy TeamCity agent pinned to `main-server`.
- Force-deleted the stale terminating legacy TeamCity agent pod object.
- Cordoned `k8s-node-3` while it was `NotReady`; `main-server` was temporarily uncordoned because public ingress depends on a hostPort ingress pod there.
- Backend service endpoints are clean: `gpt/backend` points only to `10.1.140.114:8000` on `k8s-node-2`.
- Direct ingress on `k8s-node-2` returns 200 for `https://gpt-mini-app-api.lightny.pro/api/v1/models/catalog`, but direct/public traffic to `main-server` times out because the main-server ingress pod was crashlooping and then did not recreate.
- Ingress DaemonSet is stale: `metadata.generation=3`, `observedGeneration=2`, `desired/current=3`, visible pods=2. Next required console action on `main-server`: `sudo systemctl restart snap.microk8s.daemon-kubelite`, then verify ingress DaemonSet reaches 3 ready pods.

## Incident checkpoint - 2026-07-07T13:36:12.1383307+01:00

- Sentry issue GPT-MINI-APP-BACKEND-5G was traced to DNS/DB connectivity failures during MicroK8s/control-plane instability, not an application-code regression.
- Reduced main-server pressure by scaling AFFiNE workloads to zero and scaling the legacy TeamCity agent on main-server to zero; backend service currently has a healthy endpoint on k8s-node-2 (10.1.140.114:8000).
- main-server is Ready and API /readyz passes after containerd/kubelite restarts, but normal ingress DaemonSet remains stale (desired=3, eady=2) and has not recreated the main-server ingress pod.
- Temporary ingress/nginx-ingress-main-manual host-network pod is Running and can route locally to /api/v1/models/catalog, but external traffic to 152.53.95.40:80/443 still times out.
- Next action: inspect/fix main-server host firewall/routing for ports 80/443, or repoint public origin/Cloudflare to k8s-node-2 (152.53.33.181) as the fastest restore path.

## Incident checkpoint - 2026-07-07T13:48:32.8730506+01:00

- After main-server reboot, Kubernetes API readiness passes and main-server is Ready.
- Public API still fails with Cloudflare 522; direct main-server origin probe to 152.53.95.40:443 cannot connect.
- Direct k8s-node-2 origin probe to 152.53.33.181:443 returns 200 for /api/v1/models/catalog.
- Temporary host-network ingress pod on main-server is Running, but normal MicroK8s ingress DaemonSet remains stale at desired=3/ready=2 with no regular main-server ingress pod.
- Next recommended restore: repoint public origin/Cloudflare for gpt-mini-app-api.lightny.pro to k8s-node-2 (152.53.33.181) or inspect/fix main-server host firewall/routing for inbound 80/443.

## Incident checkpoint - 2026-07-07T15:33:18.7456677+01:00

- Stopped snap.microk8s.daemon-kubelite on k8s-node-3 via operator action; controller-manager and scheduler leadership moved to main-server.
- Calico kube-controllers recreated on main-server and normal ingress pod ingress/nginx-ingress-microk8s-controller-j8lsq is Running on main-server.
- CoreDNS has one working endpoint on main-server (10.1.10.236), restoring DNS for main-server workloads.
- Public https://gpt-mini-app-api.lightny.pro/api/v1/models/catalog is stable again: five consecutive probes returned HTTP 200.
- Temporary mitigation: node2 backend pod 	g-mini-backend-869b4977b-k28p2 was relabeled pp=tg-mini-backend-isolated so broken node2 DNS does not cause backend Service flapping; backend Service currently routes only to main-server backend 10.1.10.251:8000.
- Remaining issue: k8s-node-2 reports Ready but does not start new pods; CoreDNS replica and replacement backend pod are Pending there. Next action: restart snap.microk8s.daemon-containerd and snap.microk8s.daemon-kubelite on k8s-node-2, then restore the node2 backend label or delete the isolated old pod once replacement is healthy.

## Incident checkpoint - 2026-07-07T15:55:00+01:00

- Operator restarted `snap.microk8s.daemon-containerd` and `snap.microk8s.daemon-kubelite` on `k8s-node-2`.
- Codex cannot SSH into `main-server` yet: `lightny@152.53.95.40` is reachable but rejects the available public keys.
- Public API remains healthy after the node2 daemon restart: five consecutive probes to `https://gpt-mini-app-api.lightny.pro/api/v1/models/catalog` returned HTTP 200.
- Next action: verify node2 readiness, CoreDNS readiness on node2, and DNS resolution from a node2 backend pod before restoring the temporarily isolated backend endpoint.

## Incident checkpoint - 2026-07-07T16:05:00+01:00

- Headlamp is reachable again and shows `main-server` plus `k8s-node-2` Ready; `k8s-node-3` remains NotReady with unschedulable/unreachable taints.
- Codex reached `152.53.16.43` over SSH but key auth failed for `lightny`; direct node3 inspection is blocked until a public key is authorized or command output is pasted back.
- Public API remains healthy: three consecutive probes to `/api/v1/models/catalog` returned HTTP 200.
- Recommended node3 recovery: restart/inspect `snap.microk8s.daemon-containerd` and `snap.microk8s.daemon-kubelite` on node3, wait for Ready, verify CoreDNS/Calico/ingress, then uncordon only after node health is stable.

## Incident checkpoint - 2026-07-07T16:48:00+02:00

- Operator-provided cluster output shows all three nodes have recovered to Ready.
- `k8s-node-3` is `Ready,SchedulingDisabled`; its only remaining taint is `node.kubernetes.io/unschedulable:NoSchedule` and Ready=True, NetworkUnavailable=False, with a renewing lease.
- Calico is Running on all nodes, CoreDNS is Running on main-server and k8s-node-2, and ingress controller pods are Running on main-server, k8s-node-2, and k8s-node-3.
- Next action: uncordon `k8s-node-3`, then verify node status, ingress daemonset readiness, backend endpoints, and public API health.

## Incident checkpoint - 2026-07-07T17:05:00+02:00

- CyberColors database inspection shows `cybercolors-db-instance1-5r9k-0` is healthy `2/2` on `main-server` and appears to be Patroni leader/primary.
- `cybercolors-db-instance1-7pb7-0` on `k8s-node-2` and `cybercolors-db-instance1-qfqz-0` on `k8s-node-3` are `1/2` because their `database` container readiness returns 503; the sidecar is ready.
- Logs show standby startup/catch-up failure on at least `7pb7`: it follows leader `5r9k` but waits for WAL at `C/74000040`, while primary reports no more WAL on that timeline and pgBackRest archive retrieval is not usable (`archive-get command requires option: pg1-path`).
- Recommended fix: verify Patroni roles with `patronictl list`, then reinitialize only the affected replica member(s) with `patronictl reinit`; do not restart/delete the primary.

## Incident checkpoint - 2026-07-07 main-server memory balancing

- Cluster is service-healthy again per operator report and public API probes; public `/api/v1/models/catalog` returned 200 in three consecutive checks.
- Headlamp shows `main-server` still has the highest memory pressure, about 10.2Gi/15.6Gi (65.6%).
- Codex still cannot SSH to `main-server`; key auth for `lightny@152.53.95.40` is rejected, so live pod/process memory attribution requires operator command output or SSH authorization.
- Next action: identify main-server memory owners with `kubectl top` plus host `ps`; avoid full drain because main-server runs control-plane and DB/ingress responsibilities. Prefer cordoning or workload-level scaling/migration for movable non-critical workloads.

## Incident checkpoint - 2026-07-07 main-server memory attribution

- Operator-provided resource output shows `main-server` at 10547Mi/66%, `k8s-node-2` at 6483Mi/42%, and `k8s-node-3` at 3680Mi/23%.
- Biggest pod memory users cluster-wide: YouTrack (~2009Mi), TeamCity server (~1651Mi), Prometheus (~1224Mi), CyberColors indexer worker (~818Mi), TeamCity agents on node2/node3 (~761-928Mi).
- Main-server pod list confirms many local-PV/stateful workloads are pinned there: TeamCity server, YouTrack, multiple Postgres/DB pods, Redis, Loki, Prometheus, registry, VPN DB, and app pods.
- Host process list matches pod attribution: YouTrack Java RSS ~1.96Gi, TeamCity Java RSS ~1.54Gi, Prometheus RSS ~1.33Gi, CyberColors AI worker Python RSS ~0.92Gi, kubelite RSS ~0.91Gi.
- Do not cordon/drain main-server globally because local-PV workloads and control-plane responsibilities depend on it. Use targeted stateless workload placement away from main-server plus memory limits/tuning for TeamCity, YouTrack, Prometheus, and indexer.

## Incident checkpoint - 2026-07-07 storage and Prometheus plan

- Prometheus currently runs with `--storage.tsdb.retention.time=10d`; setting retention to `15d` would increase retention, not lower it.
- Prometheus is managed by the live kube-prometheus stack rather than manifests in this backend repo; change retention through the Prometheus CR or Helm values, then persist in the cluster GitOps/Helm source of truth.
- For node-agnostic non-database PVCs, `microk8s-hostpath` is the blocker because it creates node-affine local storage. Use a shared/distributed CSI storage class for movable app PVCs, such as Longhorn, Rook-Ceph, NFS CSI, or an equivalent Hetzner/network-backed CSI.
- Databases should stay on database-native HA/replication or operator-managed storage; do not treat PostgreSQL PVCs as freely movable unless the storage backend and DB operator explicitly support the failure mode.

## Incident checkpoint - 2026-07-07 Longhorn sizing discussion

- User wants to move non-database app PVCs from `microk8s-hostpath` to Longhorn and eventually delete the 500GB disk from `main-server`.
- Nextcloud should be excluded from capacity analysis because it is not needed currently.
- Repository-visible PVCs only show TeamCity extra agents at 30Gi each and mini-app Postgres data at 10Gi per replica; most live PVC sizes/usages are cluster state and need `kubectl get pvc,pv` plus filesystem usage for accurate sizing.
- Planning guidance: with dedicated Longhorn disks, reserve around 10% free space; replica count 3 costs 3x raw usage, replica count 2 costs 2x and is a practical default for non-DB app PVCs with backups.
- Preliminary recommendation before exact PVC inventory: 3x200GB dedicated disks is the practical minimum for non-DB app PVCs excluding Nextcloud; 3x300GB is safer if TeamCity/YouTrack/Prometheus/Loki/registry history should have growth room. 3x100GB is likely too tight.

## Incident checkpoint - 2026-07-07 Longhorn disk inventory

- Operator added 300GB secondary disks to each of the three servers; disks still need partitioning/mounting for Longhorn.
- PVC inventory excluding Nextcloud requests about 332Gi total. Main-server non-Nextcloud PVC requests about 232Gi, including databases and app PVCs.
- Non-database/non-Nextcloud app PVCs are about 156Gi requested: TeamCity agents/data/logs, YouTrack, registry, Affine config/storage, Teamspeak, VPN/XUI app PVCs, and small Redis-like state.
- With three 300GB disks, practical Longhorn usable capacity is roughly 376Gi logical at replica count 2, or 251Gi logical at replica count 3 after reserving around 10% free space. Therefore 3x300GB is sufficient for non-database app PVCs with r2, but too tight for all non-Nextcloud PVCs at r3.
- Recommended rollout: mount each new disk at `/var/lib/longhorn`, install Longhorn prerequisites, create `longhorn-r2` for normal app PVCs and keep DB PVC migration separate.

## Incident checkpoint - 2026-07-07 Longhorn disk mount prep

- New empty Longhorn disks identified by operator output: `main-server` has `/dev/vdc` at 300G; `k8s-node-2` and `k8s-node-3` have `/dev/vdb` at 300G.
- Existing `main-server` `/dev/vdb` is the 500G `/mnt/second_drive` Nextcloud disk and must not be formatted during Longhorn setup.
- Codex SSH remains blocked on all nodes due to rejected public keys, so disk formatting/mounting must be run by the operator unless SSH access is authorized.
- Added `k8s/longhorn-storageclasses.yaml` with `longhorn-r2` and `longhorn-r3`, both using `Retain` reclaim policy and ext4.
- Next operational step: format and mount the new 300G disks at `/var/lib/longhorn` on all nodes, install `open-iscsi` and `nfs-common`, install Longhorn, apply storage classes, then migrate one low-risk app PVC first.

## Incident checkpoint - 2026-07-07 Longhorn disks mounted

- Operator reported completing the Longhorn disk mounting step after mounting `/var/lib/longhorn` on `main-server` and then repeating on the other two nodes.
- Codex still cannot SSH to verify mounts directly; public API remained healthy during the check.
- Next step: verify `/var/lib/longhorn` on all nodes, install `open-iscsi`/`nfs-common`, install Longhorn v1.12.0 from the official manifest, apply `longhorn-r2`/`longhorn-r3` StorageClasses, and validate Longhorn nodes/disks before migrating PVCs.

## Incident checkpoint - 2026-07-07 Longhorn driver deployer MicroK8s fix

- Longhorn managers, engine images, instance managers, UI, and node disks are running/registered on all three nodes.
- `longhorn-driver-deployer` is CrashLooping because it cannot auto-detect kubelet root: logs say `failed to get arg root-dir. Need to specify "--kubelet-root-dir"` after failing to find normal `kubelet` or `k3s` processes.
- Cause: MicroK8s uses `kubelite`, so Longhorn's auto-detection misses the kubelet root path.
- Required fix: patch `longhorn-driver-deployer` command to include `--kubelet-root-dir /var/snap/microk8s/common/var/lib/kubelet`, then restart the deployer and verify CSI pods appear.

## Incident checkpoint - 2026-07-07 Longhorn post-patch verification pending

- User reports the MicroK8s kubelet-root patch appears to have helped Longhorn.
- Codex still cannot SSH to `main-server`, so direct cluster inspection is blocked by key auth.
- Public mini-app API remained healthy during verification: three probes to `/api/v1/models/catalog` returned HTTP 200.
- Next required output: Longhorn pod/daemonset/job/node/storageclass state to confirm CSI deployment completed and `longhorn-driver-deployer` stopped CrashLooping.

## Incident checkpoint - 2026-07-07 Longhorn validated

- Local `kubectl` context `microk8s` works from Codex, so cluster inspection no longer depends on SSH for Kubernetes API reads/writes.
- Longhorn is healthy after applying the MicroK8s kubelet-root patch: all Longhorn managers, engine images, instance managers, CSI controllers, and `longhorn-csi-plugin` pods are Running.
- Longhorn nodes are all Ready/Schedulable: `main-server`, `k8s-node-2`, and `k8s-node-3`.
- Patched installer-created `longhorn` StorageClass to non-default, leaving `microk8s-hostpath` as the only default StorageClass.
- Applied `k8s/longhorn-storageclasses.yaml`, creating explicit `longhorn-r2` and `longhorn-r3` StorageClasses with `Retain` reclaim policy.
- Smoke-tested `longhorn-r2` with a disposable 1Gi PVC and pod; pod mounted `/dev/longhorn/...`, wrote/read `longhorn-ok`, and Longhorn volume was `attached` and `healthy`.
- Cleaned up the smoke-test pod, PVC, released PV, and retained Longhorn volume. No Longhorn volumes remain after cleanup.
- Public mini-app API remained healthy after Longhorn validation.

## Incident checkpoint - 2026-07-07 PVC migration shortlist

- Built live PVC/workload map for Longhorn migration planning.
- Excluded databases and Nextcloud from first-pass migration candidates.
- Strong first test candidates: `affine/affine-config-pvc` and `affine/affine-storage` because Affine is scaled to 0; `default/ts3-storage` because it is small; TeamCity agent PVCs because they are build-agent state/cache and not production user traffic.
- Main-server disk relief candidates, in increasing risk: Affine app PVCs, Teamspeak PVC, TeamCity logs/data, registry claim, YouTrack PVC. Registry and YouTrack need backups/rollback planning before migration.
- Avoid first-pass migration for Postgres/CrunchyData PVCs, mini-app DB PVCs, CyberColors DB PVCs, Redis production PVCs, Remnawave DB, and Nextcloud PVCs.

## Incident checkpoint - 2026-07-07T19:10:30+01:00 Affine Longhorn PVC migration

- Migrated non-database Affine PVCs only: `affine-config-pvc` and `affine-storage` were copied to `affine-config-pvc-longhorn` and `affine-storage-longhorn`.
- Added `longhorn-r2-local` StorageClass with `numberOfReplicas=2`, `Retain`, ext4, and `dataLocality=best-effort` after the initial `longhorn-r2` target failed first mount for the 30Gi volume when both replicas were remote from `main-server`.
- Verified the prepare job mounted and formatted both Longhorn target PVCs on `main-server`; copy job copied 24Ki config and 15.5Mi storage.
- Patched `affine/deployment affine` to use the Longhorn PVCs, then scaled `postgres`, `redis`, and `affine` back to 1 replica.
- Verified Affine rolled out successfully, public `https://affine.kosh.games/` returned HTTP 200, and both migrated Longhorn volumes are healthy attached on `k8s-node-3`.
- Old Affine PVCs remain intact for rollback: `affine-config-pvc` and `affine-storage`; database PVC `pgdata-postgres-0` was not migrated.

## Incident checkpoint - 2026-07-07T20:05:00+01:00 TeamCity Longhorn PVC migration

- Migrated TeamCity server PVCs from `microk8s-hostpath` to Longhorn:
  - `gamedev/teamcity-data-pvc` -> `teamcity-data-pvc-longhorn`
  - `gamedev/teamcity-logs-pvc` -> `teamcity-logs-pvc-longhorn`
- Copied about 5.6Gi of TeamCity data and 101Mi of logs into the Longhorn target PVCs.
- Patched `gamedev/deployment teamcity-server-deployment` to mount the Longhorn PVCs and added node affinity excluding `main-server`.
- TeamCity server restarted on `k8s-node-3`; startup returned temporary 503 while TeamCity rebuilt `idea.zip` under `system/caches/agentTools`.
- TeamCity completed startup after about 15 minutes; public `https://teamcity.kosh.games/` now returns HTTP 401, TeamCity logs show `Server Started` and `Current stage: System is ready`, and both k8s-node agents re-registered.
- Longhorn reports both TeamCity volumes as `attached healthy` on `k8s-node-3`.
- Cleaned up one-off migration jobs `teamcity-longhorn-prepare` and `teamcity-copy-to-longhorn`.
- Old hostpath PVCs remain intact for rollback: `teamcity-data-pvc` and `teamcity-logs-pvc`.
- After the move, `kubectl top nodes` showed `main-server` memory around 9793Mi/61%, down from the earlier 65-72% range.

## Incident checkpoint - 2026-07-07T21:10:00+01:00 TeamCity third agent restored

- Scaled legacy `gamedev/deployment teamcity-agent-deployment` from 0 to 1 replica.
- The restored agent is pinned to `main-server` and uses distinct `AGENT_NAME=gamedev-teamcity-agent`, separate from the node2/node3 agents.
- Pod `teamcity-agent-deployment-68c4fd7fb4-ll4rm` is Running on `main-server`.
- Agent logs show Docker client/server/compose are available, plugin sync completed, and the agent registered with TeamCity as id `301`.
- TeamCity remained reachable; public `https://teamcity.kosh.games/` returned HTTP 401.
- Current TeamCity worker pods are running on all three nodes: legacy main-server agent plus `gamedev-teamcity-agent-k8s-node-2` and `gamedev-teamcity-agent-k8s-node-3`.
- After restoring the third agent, `kubectl top nodes` showed `main-server` around 10495Mi/66% memory.

## Incident checkpoint - 2026-07-07T21:25:00+01:00 YouTrack Longhorn PVC migration

- Migrated `gamedev/youtrack-pvc` from `main-storage` hostpath to `youtrack-pvc-longhorn` on Longhorn.
- Preserved RWX access mode because `gamedev/cronjob google-drive-backup` mounts the same PVC for Google Drive sync.
- Copied the full YouTrack PVC, including `data`, `conf`, `logs`, and `backups`; copy job reported 15.6G copied.
- Patched `gamedev/deployment youtrack-deployment` to use `youtrack-pvc-longhorn` and added node affinity excluding `main-server`.
- Patched `gamedev/cronjob google-drive-backup` to use `youtrack-pvc-longhorn`; temporarily suspended it during copy and resumed it after YouTrack was healthy.
- YouTrack restarted successfully on `k8s-node-3`; public `https://youtrack.kosh.games/` returned HTTP 200 and logs reached `Application state changed from APP_LIFECYCLE_START to RUNNING`.
- Longhorn reports volume `pvc-a9884e83-bda0-4e4b-88bf-44e4e150035d` as `attached healthy` on `k8s-node-3`.
- Cleaned up the one-off `youtrack-copy-to-longhorn` job.
- Old rollback PVC remains intact: `gamedev/youtrack-pvc` on `main-storage`.
- After the move, `kubectl top nodes` showed `main-server` around 9047Mi/56% memory, down from about 10385Mi/65% before the YouTrack migration.

## Incident checkpoint - 2026-07-08 old rollback PVC cleanup

- Verified migrated workloads still use Longhorn PVCs before deleting rollback storage:
  - `affine/deployment affine` uses `affine-storage-longhorn` and `affine-config-pvc-longhorn`.
  - `gamedev/deployment teamcity-server-deployment` uses `teamcity-data-pvc-longhorn` and `teamcity-logs-pvc-longhorn`.
  - `gamedev/deployment youtrack-deployment` uses `youtrack-pvc-longhorn`.
  - `gamedev/cronjob google-drive-backup` is resumed and uses `youtrack-pvc-longhorn`.
- Public health checks before cleanup: Affine returned HTTP 200, YouTrack returned HTTP 200, TeamCity returned expected unauthenticated HTTP 401.
- Deleted completed pre-migration Google Drive backup jobs `google-drive-backup-29721600` and `google-drive-backup-29723040` because their completed pods still referenced old `gamedev/youtrack-pvc`.
- Deleted old rollback PVCs:
  - `affine/affine-config-pvc`
  - `affine/affine-storage`
  - `gamedev/teamcity-data-pvc`
  - `gamedev/teamcity-logs-pvc`
  - `gamedev/youtrack-pvc`
- Deleted remaining released hostpath PVs for old TeamCity data and old YouTrack; the other old hostpath PVs had already disappeared by verification time.
- Verified only the replacement Longhorn PVs remain for Affine, TeamCity server, and YouTrack, and all corresponding Longhorn volumes are `attached healthy`.
- Temporary node debug check on `main-server` after cleanup showed root `/` at 364G used / 119G free (76%), `/mnt/second_drive` at 216G used / 251G free (47%), and `/var/lib/longhorn` at 18G used / 262G free (7%).
- Deleted the temporary `node-debugger-main-server-v9c8w` pod.
- One unrelated issue remains: `cybercolors-indexer-worker-7f64c66b4-zqbq4` is Pending and was not part of this PVC cleanup.

## Incident checkpoint - 2026-07-08 CyberColors indexer pending pod fix

- The pending `cybercolors-indexer-worker-7f64c66b4-zqbq4` was not caused by replica count; the Deployment already desired 1 replica.
- The rollout was stuck with 2 total pods because the new template had contradictory scheduling rules: `nodeSelector` pinned it to `main-server`, while required node affinity allowed only `k8s-node-2` and `k8s-node-3`.
- Removed the bad `spec.template.spec.affinity` from `cybercolors/deployment cybercolors-indexer-worker`, matching the last-applied manifest intent of one worker pinned to `main-server`.
- Rollout completed successfully; final state is `cybercolors-indexer-worker` 1/1 with pod `cybercolors-indexer-worker-7fd85b7fbd-477vc` Running on `main-server`.
- `kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded` returned no resources afterward.

## Incident checkpoint - 2026-07-08 CyberColors indexer moved off main-server

- User correctly challenged the earlier live-only fix: the CyberColors indexer worker should not remain on `main-server` after the load-balancing work.
- Root cause was the GitOps source manifest in `cybercolors_bot/deploy/k8s/cybercolors/workers.yaml`, which pinned `cybercolors-indexer-worker` with `nodeSelector: kubernetes.io/hostname=main-server`.
- A live patch to require `k8s-node-2`/`k8s-node-3` was immediately reverted by ArgoCD self-heal because the Argo app `cybercolors` tracks `https://github.com/l1ghtny/cybercolors_bot.git`, path `deploy/k8s/cybercolors`, target `master`.
- Updated the GitOps source manifest to remove the `main-server` nodeSelector and add required node affinity for `k8s-node-2` or `k8s-node-3`.
- Committed and pushed CyberColors repo commit `b3a74e6` (`Move CyberColors indexer worker off main server`) to `origin/master`.
- Refreshed ArgoCD app `cybercolors`; app synced to revision `b3a74e6141020958de99429ef117dafadd81bcbd` and reported Healthy.
- Final worker state: exactly one pod, `cybercolors-indexer-worker-84f5876d6d-6xpxl`, Running on `k8s-node-3`; no non-running pods remained cluster-wide.
- Final node memory after move: `main-server` around 8312Mi/52%, `k8s-node-2` around 6819Mi/44%, `k8s-node-3` around 9659Mi/62%.


## 2026-07-10 GPT-5.6 tier upgrade

- Upgraded the active OpenAI paid tiers while keeping the Fast tier on `gpt-5.4-nano`:
  - Smart: `gpt-5.4-mini` -> `gpt-5.6-luna`
  - Balanced: `gpt-5.4` -> `gpt-5.6-terra`
  - Flagship: `gpt-5.5` -> `gpt-5.6-sol`
- Added backend legacy-id canonicalization, shared usage buckets, provider/display mappings, and premium-sample updates.
- Added migration `n1a2b3c4d5e6_upgrade_openai_text_models_to_gpt_5_6.py` for user/conversation defaults, entitlement limits, catalog metadata, and standard API pricing.
- Preserved historical request/token model ids while clearing OpenAI chain state for migrated conversations.
- Added GPT-5.6 reasoning efforts `none`, `low`, `medium`, `high`, `xhigh`, and `max`; reasoning-off now sends explicit `none`.
- Added hashed OpenAI `safety_identifier` values and split reasoning tokens from total output tokens to prevent double charging.
- Updated frontend model-id normalization, labels, premium sample copy, and catalog capability types.

### Verification

- Backend targeted tests: 26 passed.
- Backend Python compilation: passed.
- Alembic heads: `n1a2b3c4d5e6 (head)`.
- Frontend Vitest: 3 passed.
- Frontend production build: passed with existing chunk-size/dynamic-import warnings.

### Next steps

- Apply `poetry run alembic upgrade head` in the target environment during the backend rollout.
- Canary one send per tier and verify model id, SSE completion, reasoning-off behavior, entitlement decrement, and recorded cost before promotion.
