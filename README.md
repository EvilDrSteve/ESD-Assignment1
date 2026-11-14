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
 (HTTP + gRPC)   :10000 / :10001 └──► replica C (HTTP + gRPC) ─┘

 Prometheus scrapes A, B, C and Envoy directly ──► Grafana
 replica logs + Envoy access logs ──► Filebeat ──► Elasticsearch ──► Kibana
```

**Status.** The code on this branch is still the finished A2: one HTTP container
and one gRPC container, no Envoy yet. Everything in the "Run" section below
describes that A2 setup until the A3 issues land.

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

| Thing | Where it lives now | Problem with 3 replicas |
|---|---|---|
| Products, carts, orders | Redis | None: all replicas share it |
| Idempotency records | Redis | None: duplicate prevention still works |
| Rate-limit counters | In each process's memory | Each replica counts separately, so a client gets ~3× the limit (#4) |
| Client IP for the rate limit | Socket address | Behind Envoy every request comes from Envoy's IP (#4) |
| Fault switch | One Redis key | Slows every replica; experiments need B alone (#5) |

The A3 brief is summarised in issue #1.

```
           HTTP :8050                          gRPC :50051
 curl / http_caller.py ──► app (FastAPI) ─┐  ┌─ grpc-server ◄── grpc_client.py
                                          ▼  ▼
                              orders_service.py (shared rules)
                                          │
                                    Redis (source of truth)

 Prometheus scrapes app:8050/metrics and grpc-server:9101 ──► Grafana :3000
 app.log + grpc.log ──► Filebeat ──► Elasticsearch ──► Kibana :5601
```

## Prerequisites

- Docker with Docker Compose v2
- Python 3.12 on the host for the tests and clients
- Free ports: 8050, 50051, 9101, 9090, 9100, 3000, 9200, 5601

## Run

```bash
docker compose up -d --build
```

This starts 10 containers: `redis`, `app` (HTTP), `grpc-server`, Prometheus,
Grafana, Node Exporter, Elasticsearch, `es-init` (creates the index template,
then exits), Kibana and Filebeat. Give it about 30 s.

| Service | URL |
|---------|-----|
| HTTP API | http://localhost:8050 |
| Swagger UI (OpenAPI) | http://localhost:8050/docs |
| gRPC | localhost:50051 (`store.v1.OrderService`, reflection enabled) |
| gRPC metrics | http://localhost:9101/metrics |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin / admin) |
| Kibana | http://localhost:5601 |

Grafana's Prometheus datasource and both dashboards (**Store → Online Store
Dashboard** for A1, **Store → Store A2 — HTTP vs gRPC: Place order**) are
provisioned from `grafana/`, so nothing has to be set up by hand.

Kibana: create a data view `store-logs-*` with `@timestamp`, then search, e.g.
`request_id : "trace-demo-1"` or `service : "store-grpc" and grpc_code : *`.

Stop: `docker compose down` (add `-v` to delete the data volumes).

## Host environment (tests and clients)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Tests

```bash
pytest -q          # 37 tests: HTTP API, gRPC, caller, OpenAPI contract
```

Tests use an in-memory Redis (fakeredis) and an in-process gRPC server, so the
stack does not need to be running.

| File | Covers |
|------|--------|
| `tests/test_http_api.py` | valid requests, invalid input, repeated requests (idempotency), 429 + Retry-After, faults, request ids |
| `tests/test_grpc.py` | gRPC success, FAILED_PRECONDITION, INVALID_ARGUMENT, shared data and idempotency with HTTP, gRPC metrics |
| `tests/test_caller.py` | retry/backoff, no retry when unsafe, circuit breaker open → half-open → closed |
| `tests/test_openapi.py`, `tests/test_contract.py` | `openapi.yaml` matches the code; live responses validated against the spec |

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
| POST | `/admin/faults` | Demo only: `{"mode": "error"\|"slow"\|"off", "duration_s": 10}` |

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

`POST /orders`: **5 requests per 10 s window per client IP** (in-process,
single instance; `ORDERS_RATE_LIMIT` / `ORDERS_RATE_WINDOW_S`). The 6th call
in a window gets `429` with `Retry-After` (seconds until the window ends).

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

## gRPC (A2 Part B)

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
