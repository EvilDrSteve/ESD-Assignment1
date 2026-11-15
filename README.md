# Online Store API — ESD Assignments 1, 2 & 3

An online store with an HTTP API (FastAPI) and a gRPC service, sharing one Redis
store, with Prometheus metrics, Grafana dashboards and an ELK logging pipeline.

- **A1:** API, metrics, dashboards, logging pipeline (see `report.md`)
- **A2:** improved HTTP API, OpenAPI spec, idempotency, rate limiting, reliable
  Python caller, gRPC `PlaceOrder` service, extended metrics (see `report-a2.md`)
- **A3 (in progress):** three replicas behind Envoy, load-balancing
  experiments. See the next section.

## Assignment 3: plan

**Goal.** Run three copies of the store (replicas **A**, **B**, **C**) and put
**Envoy** in front of them. Every HTTP request and gRPC call goes to Envoy, and
Envoy picks a replica. Prometheus, Grafana and Kibana then show how the
traffic was spread, followed by experiments: uneven weights, a slow replica, and a
replica that crashes and comes back. The full brief is summarised in
[issue #1](https://github.com/EvilDrSteve/ESD-Assignment1/issues/1).

```
                                 ┌──► replica A (HTTP + gRPC) ─┐
 load client ──► Envoy ──────────┼──► replica B (HTTP + gRPC) ─┼──► Redis (shared data)
 (HTTP + gRPC)   :8050 / :50051  └──► replica C (HTTP + gRPC) ─┘

 Prometheus scrapes A, B, C and Envoy directly ──► Grafana
 replica logs + Envoy access logs ──► Filebeat ──► Elasticsearch ──► Kibana
```

**Status.** Done: #2–#6. Three replicas (A, B, C) each serve HTTP and gRPC
in one process. Envoy listens on the A2 ports (8050 HTTP, 50051 gRPC) and
sends everything to **A** (Part A). All A2 commands below now go through
Envoy unchanged.

**Plan.** The work is tracked as GitHub issues; each lists the issues it needs
first.

| Step | Issues | What |
|---|---|---|
| 1. Make the app work as 3 replicas | #2, #3, #4, #5 | HTTP + gRPC in one container scaled to 3, read-only `GetProduct` RPC + gRPC health check, rate limit shared in Redis, per-replica slow-down switch |
| 2. Envoy and observability | #6, #7, #8, #9, #10 | Envoy (A only at first), Prometheus per replica, Envoy logs in Kibana, fixed-rate load client, per-replica Grafana dashboard |
| 3. Baselines | #11, #12 | Everything to A, then round robin across A, B, C |
| 4. Experiments | #13, #14, #15 | Weights 2:1:1, slow B (round robin vs least request), stop and restart B with health checks |
| 5. Write-up | #16 | README and report |

#2 comes first: most other issues build on it.

**Workflow.** One branch per issue off `assignment-3`
(e.g. `git switch -c 2-replicas`), merged back into `assignment-3` with a PR.

**Things that change in A3 and why** (details in each issue):

| Thing | In A2 | Problem with 3 replicas | A3 |
|---|---|---|---|
| Products, carts, orders | Redis | None: all replicas share it | unchanged |
| Idempotency records | Redis | None: duplicate prevention still works | unchanged |
| Rate-limit counters | Each process's memory | Each replica counts separately, so a client gets ~3× the limit | Redis key per client (#4) |
| Client IP for the rate limit | Socket address | Behind Envoy every request comes from Envoy's IP | last `X-Forwarded-For` entry (#4) |
| Fault switch | One Redis key | Slows every replica; experiments need B alone | `fault:B` / `fault:all` (#5) |

The A3 brief is summarised in issue #1.

### One replica

```
        HTTP :8050                         gRPC :50051
            │                                   │
   ┌────────▼───────── app container (serve.py, one process) ─────▼────────┐
   │  FastAPI app (main.py)                 grpc.server (grpc_server.py)    │
   │        └──────────► orders_service.py (shared rules) ◄────────┘        │
   │  /metrics on :8050: HTTP + gRPC metrics      logs: app-<replica>.log   │
   └────────────────────────────────┬──────────────────────────────────────┘
                                     ▼
                               Redis (shared)
```

Compose starts three copies (`deploy.replicas: 3`): containers
`assignment-1-app-1/2/3`. Scaled copies share one environment, so each works
out its name at startup from Docker's reverse DNS (`app-2` → **B**, see
`app/replica.py`). That name is in every log line (`"replica": "B"`) and in
the log file name.

### Envoy (`envoy/envoy.yaml`)

| Listener | Port | Cluster | Upstream protocol |
|---|---|---|---|
| `http` | 8050 | `store_http` → replicas' :8050 | HTTP/1.1 |
| `grpc` | 50051 | `store_grpc` → replicas' :50051 | HTTP/2 |

Both listeners are HTTP connection managers (layer 7), not TCP proxies, so
Envoy chooses a replica for **every request or call**. With a TCP proxy a
gRPC client's single long-lived connection would pin all its calls to one
replica. Envoy keeps the caller's `X-Request-ID` (`preserve_external_request_id`),
appends the caller's address to `X-Forwarded-For`, and writes one JSON access
log line per request with `request_id`, `upstream_host` and `replica` (from
the `X-Replica` response header each replica sets).

Current config (Part C, the starting point for every experiment): both
clusters list A, B and C with `load_balancing_weight: 1` and
`lb_policy: ROUND_ROBIN`. The Part A/B baseline listed A only. After editing
`envoy/envoy.yaml`, apply it with `docker compose up -d --force-recreate envoy`.
Check it with `curl -s localhost:9901/clusters | grep -E "hostname|health_flags"`.

**Experiment configs.** `scripts/build_envoy_variants.py` writes one Envoy
config per experiment into `envoy/`. Each is the base config with only the
experiment's lines changed. Re-run the script after editing `envoy.yaml`.
Compose mounts `envoy/$ENVOY_CONFIG`, defaulting to `envoy.yaml`:

```bash
ENVOY_CONFIG=weighted-2-1-1.yaml docker compose up -d --force-recreate envoy   # switch
docker compose up -d --force-recreate envoy                                   # restore 1:1:1
```

| Variant file | Experiment |
|---|---|
| `weighted-2-1-1.yaml` | D1: weighted round robin, A:B:C = 2:1:1 |
| `least-request.yaml` | D2: `LEAST_REQUEST` (power of two choices), equal weights |

D2 (slow B, round robin vs least request):

```bash
bash scripts/fault.sh B delay 200 900
python clients/loadgen.py --interface both --rate 50 --duration 60 --label delayb-rr
ENVOY_CONFIG=least-request.yaml docker compose up -d --force-recreate envoy
python clients/loadgen.py --interface both --rate 50 --duration 60 --label delayb-lr
docker compose up -d --force-recreate envoy
bash scripts/fault.sh B off && bash scripts/fault.sh all status   # recover: delay 0 everywhere
```

Runs so far (`evidence/a3/runs/`, details in `report-a3.md`):

| Run | Envoy config | HTTP A / B / C | gRPC A / B / C |
|---|---|---|---|
| `baseline-a-only` | A only | 3000 / 0 / 0 | 3000 / 0 / 0 |
| `rr-equal` | Round robin 1:1:1 | 1000 / 999 / 1001 | 1000 / 1000 / 1000 |
| `wrr-2-1-1` | Weighted round robin 2:1:1 | 1499 / 751 / 750 | 1500 / 750 / 750 |
| `delayb-rr` | Round robin, B +200 ms | 999 / 1002 / 999 | 1000 / 1000 / 1000 |
| `delayb-lr` | Least request, B +200 ms | 1285 / 445 / 1270 | 1258 / 426 / 1316 |

The load client also prints and saves client p50/p95 **per replica**
(`latency_ms_by_replica`), so a slow replica stands out directly.

## Prerequisites

- Docker with Docker Compose v2
- Python 3.12 on the host for the tests and clients
- Free ports: 8050, 50051, 9901, 9090, 9100, 3000, 9200, 5601

## Run

```bash
docker compose up -d --build --remove-orphans   # --remove-orphans drops the old A2 grpc-server container
docker compose ps                               # assignment-1-app-1, -2, -3 and envoy
```

This starts 13 containers: `redis`, three `app` replicas, `envoy`,
Prometheus, Grafana, Node Exporter, Elasticsearch, `es-init` (creates the
index template, then exits), Kibana and Filebeat. Give it about 30 s.

| Service | URL |
|---------|-----|
| HTTP API (through Envoy) | http://localhost:8050 |
| Swagger UI (OpenAPI) | http://localhost:8050/docs |
| gRPC (through Envoy) | localhost:50051 (`store.v1.OrderService`, `grpc.health.v1.Health`, reflection enabled) |
| Envoy admin | http://localhost:9901 (`/clusters`, `/stats/prometheus`) |
| Envoy access log | `docker exec envoy tail -f /var/log/envoy/access.log` (JSON, one line per request/call) |
| Replica logs | `docker compose logs app` or `/var/log/app/app-A.log` (B, C) in the `app_logs` volume |
| Prometheus | http://localhost:9090 (job `store`: `assignment-1-app-1/2/3:8050` with label `replica` A/B/C; job `envoy`: `envoy:9901/stats/prometheus`) |
| Grafana | http://localhost:3000 (admin / admin) |
| Kibana | http://localhost:5601 |

Grafana's Prometheus datasource and the dashboards (**Store → Online Store
Dashboard** for A1, **Store → Store A2 — HTTP vs gRPC: Place order**,
**Store → Store A3 — Load balancing per replica**) are provisioned from
`grafana/`, so nothing has to be set up by hand.

### A3 dashboard (`grafana/dashboards/store-a3.json`)

Built by `scripts/build_a3_dashboard.py` (edit there, then re-run). The
`rate window` dropdown sets `$window` (**30s** / 1m / 2m); every panel title
shows the window it uses. 30s is the default because a run lasts 60 s: with
1m the rate never reaches a flat plateau. Graphs step at 2 s (the scrape
interval) instead of Grafana's 15 s default. Replicas have fixed colours: A
blue, B orange, C green. Application traffic only: `/metrics`, `/health` and
gRPC health checks are excluded.

| Panel | Query (HTTP; gRPC uses `grpc_server_*` the same way) |
|---|---|
| Share per replica over the time range | one query per replica: `100 * (sum(increase(http_requests_total{…,replica="A"}[$__range])) or vector(0)) / scalar(sum(increase(http_requests_total{…}[$__range])))` (`or vector(0)` shows an idle replica as 0%) |
| Request rate per replica | `sum by (replica) (rate(http_requests_total{job="store",endpoint!~"/metrics\|/health"}[$window]))` |
| Share of total (%) | `100 * sum by (replica) (rate(…[$window])) / scalar(sum(rate(…[$window])))` |
| Active requests | `sum by (replica) (http_requests_in_flight{…})` (gauge, sampled every 2 s) |
| p50 / p95 | `histogram_quantile(0.95, sum by (le, replica) (rate(http_request_duration_seconds_bucket{…}[$window])))` |
| Errors by status | `sum by (replica, status) (rate(http_requests_total{…,status=~"4..\|5.."}[$window]))`; gRPC: `grpc_code!="OK"` |
| Envoy per cluster | `rate(envoy_cluster_upstream_rq_total[$window])`, `envoy_cluster_upstream_rq_active`, `envoy_cluster_membership_healthy` / `_total` |
| Replicas up, injected delay | `up{job="store"}`, `injected_delay_seconds` |

Set the time range to one experiment run (the load client prints its start
and end times) and the share panels give that run's A/B/C split.

Kibana: create a data view `store-logs-*` with `@timestamp`, then search, e.g.
`request_id : "trace-demo-1"`, `transport : "grpc" and grpc_code : *` or
`replica : "B"`.

Replica lines have `log_source : "app"`, Envoy access-log lines
`log_source : "envoy"`. Both carry the same `request_id`, so one search finds
the proxy event and the replica event of one request (`.keyword` = exact match):

| Interface | Kibana search | Returns |
|---|---|---|
| HTTP | `request_id.keyword : "trace-http-t1"` | Envoy line (`upstream_cluster: store_http`, `upstream_host`, `replica`, `response_code`) + replica line (`message: request`, `endpoint`, `status`) |
| gRPC | `request_id.keyword : "trace-grpc-t1"` | Envoy line (`upstream_cluster: store_grpc`, `path: /store.v1.OrderService/GetProduct`, `grpc_status`) + replica line (`message: rpc`, `grpc_code`) |

Send your own traced calls with `curl -H "X-Request-ID: my-id" localhost:8050/products/p1`
or `python clients/grpc_client.py ... --request-id my-id`. Saved examples:
`evidence/a3/log-examples.md`.

Stop: `docker compose down` (add `-v` to delete the data volumes).

## Host environment (tests and clients)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Tests

```bash
pytest -q          # 57 tests: HTTP API, gRPC, caller, OpenAPI contract, replica naming, faults
```

Tests use an in-memory Redis (fakeredis) and an in-process gRPC server, so the
stack does not need to be running.

| File | Covers |
|------|--------|
| `tests/test_http_api.py` | valid requests, invalid input, repeated requests (idempotency), 429 + Retry-After, faults, request ids |
| `tests/test_grpc.py` | gRPC success, FAILED_PRECONDITION, INVALID_ARGUMENT, GetProduct, health check, shared data and idempotency with HTTP, gRPC metrics |
| `tests/test_caller.py` | retry/backoff, no retry when unsafe, circuit breaker open → half-open → closed |
| `tests/test_openapi.py`, `tests/test_contract.py` | `openapi.yaml` matches the code; live responses validated against the spec |
| `tests/test_replica.py` | container name → replica letter; every log line carries `replica` |
| `tests/test_faults.py` | per-replica delay/error on HTTP and gRPC, health checks exempt, `injected_delay_seconds` |

## HTTP API (A2)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/products?category=` | List products |
| GET | `/products/{product_id}` | One product (404 if unknown) |
| GET | `/carts/{cart_id}` | Read a cart (unknown id → empty cart, nothing created) |
| PUT | `/carts/{cart_id}/items/{product_id}` | Set quantity `{"quantity": n}` (idempotent) |
| DELETE | `/carts/{cart_id}/items/{product_id}` | Remove item, 204 (idempotent) |
| POST | `/orders` | Place order `{"cart_id": ...}`, optional `Idempotency-Key` header, rate limited |
| GET | `/orders?status=` | List orders |
| GET | `/orders/{order_id}` | One order |
| PATCH | `/orders/{order_id}` | Change status `{"status": "confirmed"}` |
| GET | `/stats`, `/health`, `/metrics` | Ops |
| POST | `/admin/faults` | Demo only: `{"mode": "error"\|"slow"\|"delay"\|"off", "replica": "B"\|"all", "delay_ms": 200, "duration_s": 10}` |

All errors are `application/problem+json` (RFC 9457) with `type`, `title`,
`status`, `detail`, `instance`, `request_id` and, for validation, `errors`.

```bash
curl -s -X PUT localhost:8050/carts/my-cart/items/p1 -H "Content-Type: application/json" -d '{"quantity":2}'
curl -si -X POST localhost:8050/orders -H "Content-Type: application/json" \
     -H "Idempotency-Key: 7b0c..." -d '{"cart_id":"my-cart"}'
curl -s -X PATCH localhost:8050/orders/<id> -H "Content-Type: application/json" -d '{"status":"confirmed"}'
```

### OpenAPI

The spec is `openapi.yaml` (generated from the code; Swagger UI at `/docs`).
Regenerate after changing routes: `python scripts/export_openapi.py`.

### Rate limit

`POST /orders`: **5 requests per 10 s window per client IP**
(`ORDERS_RATE_LIMIT` / `ORDERS_RATE_WINDOW_S`). The 6th call in a window gets
`429` with `Retry-After` (seconds until the window ends).

A3: the counter is one Redis key per client (`ratelimit:orders:<ip>`, TTL =
rest of the window), so all replicas share one budget. In A2 it was in
process memory, which behind round robin would give each client three
separate budgets (about 15 orders per window instead of 5). The client IP is
the last `X-Forwarded-For` entry (added by Envoy), because the socket address
is always Envoy's.

```bash
bash scripts/rate_limit_demo.sh
```

### Reliable Python caller

```bash
python clients/http_caller.py --orders 3     # several checkouts in one run
python clients/reliability_demo.py           # success, timeout+retry, breaker open, recovery
```

Timeout 2 s, max 3 attempts (backoff 0.5 s, 1 s + jitter), circuit breaker
opens after 3 failed attempts, retries after 5 s with one trial call.

### Faults on one replica (A3)

A fault is a Redis key with a TTL: `fault:B` affects replica B only,
`fault:all` every replica (the A2 behaviour, still the default). Health checks
and `/metrics` are never affected, so a slowed replica stays healthy.

```bash
bash scripts/fault.sh B delay            # B waits 200 ms before every HTTP request and gRPC call (10 min)
bash scripts/fault.sh B delay 300 120    # 300 ms for 120 s
bash scripts/fault.sh B off
bash scripts/fault.sh all status         # injected_delay_seconds per replica, from Prometheus
```

## gRPC (A2 Part B, extended in A3)

| Service / method | What |
|---|---|
| `store.v1.OrderService/PlaceOrder` | Place an order from a cart (A2) |
| `store.v1.OrderService/GetProduct` | Read one product, `NOT_FOUND` if unknown (A3; read-only, used for load tests) |
| `grpc.health.v1.Health/Check` | Standard health check, `SERVING` while the replica runs (A3; used by Envoy) |

Proto: `app/proto/orders.proto`. Generated code is committed in `app/gen/` and
regenerated in the Docker build. Generation command (from `app/`):

```bash
python -m grpc_tools.protoc -I proto --python_out=gen --pyi_out=gen --grpc_python_out=gen proto/orders.proto
# or: bash scripts/gen_proto.sh
```

```bash
# fill a cart over HTTP (same Redis), then place the order over gRPC
curl -s -X PUT localhost:8050/carts/g1/items/p2 -H "Content-Type: application/json" -d '{"quantity":1}'
python clients/grpc_client.py --cart-id g1 --key my-key --deadline 2 --request-id trace-demo-1
python clients/grpc_client.py --cart-id empty-cart       # FAILED_PRECONDITION
python clients/grpc_client.py --cart-id "bad id!"         # INVALID_ARGUMENT
```

## Fixed-rate load client (A3)

`clients/loadgen.py` sends read-only calls through Envoy at a **fixed rate
for a fixed duration** and never waits for one response before sending the
next (open loop: call *i* is due at `start + i/rate`). HTTP uses
`GET /products/{id}`, gRPC uses `GetProduct` on one channel. There are no
retries and no circuit breaker. Each call has its own request id
(`lg-<label>-<h|g>-<n>`). Latency is measured from when the call was due.
Request ids repeat if a label is reused, so give each run its own label, or
limit the Kibana time range to the run.

```bash
python clients/loadgen.py --interface http --rate 50 --duration 60 --label baseline-http
python clients/loadgen.py --interface grpc --rate 50 --duration 60 --label baseline-grpc
python clients/loadgen.py --interface both --rate 50 --duration 60 --label baseline   # both at once
```

It prints and saves (`evidence/a3/runs/<label>.json`), per interface: target
and actual rate, sent, successes, failures by status, error rate, calls per
replica and share % (from the `X-Replica` header / `x-replica` metadata),
client p50/p95/p99, and start/end times to line up with Grafana. Example
(Part A, everything to A):

```
HTTP GET /products/{id} via localhost:8050
  rate      target 50.0/s, actual 50.02/s for 60.0 s (max send lag 10.46 ms)
  sent      3000  successes 3000  failures 0 (0.0%)  by status {'200': 3000}
  replicas  {'A': 3000}  share % {'A': 100.0}
  latency   p50 6.27 ms  p95 9.56 ms  p99 103.43 ms  max 604.11 ms
```

## Populate the dashboards

```bash
bash scripts/gen_calls.sh 20     # ~4 min: HTTP + gRPC successes, failures, replays
bash test_traffic.sh             # A1-style background traffic (Ctrl+C to stop)
```

## Order workflow

```
PENDING → CONFIRMED → SHIPPED → DELIVERED
   └────────┴──────────┴──────→ CANCELLED
```

## A1 experiments

- Fault injection: set `FAULT_INJECTION=true` on `app` in `docker-compose.yml`
  (every 5th request +500 ms, every 7th returns an error).
- Cardinality demo: `POST /demo/cardinality?mode=bad&count=100`, or
  `python app/cardinality_demo.py --with-label`.
