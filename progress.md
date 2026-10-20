# Assignment 1 Progress

## Part A — Working project (10 marks) ✅
- Online Store API built with FastAPI
- Products, cart, orders with status workflow (PENDING → CONFIRMED → SHIPPED → DELIVERED / CANCELLED)
- In-memory store, Dockerized

## Part B — Metrics and dashboards (30 marks) ✅
- prometheus_client integrated in app (`app/metrics.py`)
- All 4 metric types:
  - **Counter**: `orders_placed_total`, `orders_cancelled_total`, `http_requests_total`
  - **Gauge**: `orders_pending`, `active_carts`
  - **Histogram**: `http_request_duration_seconds` (gives p95/p99)
  - **Summary**: `order_value_dollars` (average order value)
- Middleware records HTTP metrics with normalized endpoint paths
- `/metrics` endpoint exposes Prometheus format
- Prometheus, Grafana, Node Exporter running in docker-compose
- Dashboard JSON at `grafana/dashboard.json`
- **After restart**: need to re-add Prometheus datasource (`http://prometheus:9090`) and re-import dashboard
- Node Exporter dashboard: import ID **1860** from grafana.com

## Part C — Logging pipeline (25 marks) ✅
- Structured JSON logging via `python-json-logger` (`app/logger.py`)
- Logs include: timestamp, severity, service, message, request_id, method, endpoint, status, duration_ms
- Business events logged: order_placed, order_cancelled, item_added_to_cart, warnings for 404/409
- Filebeat autodiscover reads only `service-request-app` container
- `decode_json_fields` parses app JSON into `app.*` fields in Elasticsearch
- Kibana data view: `store-logs-*` with `@timestamp`
- Search examples: `app.message: "order_placed"`, `app.severity: "WARNING"`

## Part D — System design (20 marks) ✅
- [x] Architecture diagram (app, dependencies, monitoring tools, arrows, failure scenarios)
- [x] Follow a metric through: code → Prometheus → Grafana
- [x] Follow a log through: code → Filebeat → Elasticsearch → Kibana

## Part E — Experiments (15 marks) ✅ (code ready, needs running + screenshots)
- [x] Fault injection code: set `FAULT_INJECTION=true` in docker-compose to add 500ms delay to every 5th request
- [x] Cardinality explosion script: `python app/cardinality_demo.py --with-label` vs without
- [ ] Run the experiments and capture results for the report

## Report ✅
- [x] Write-up covering sections A–E with metric table, diagrams, queries, results → `report.md`

## Useful commands
```bash
# Start everything
docker compose up --build

# Simulate traffic
bash test_traffic.sh

# Re-setup Grafana after volume wipe
# 1. Add datasource: http://prometheus:9090
# 2. Import grafana/dashboard.json
# 3. Import Node Exporter dashboard ID 1860

# Re-setup Kibana after volume wipe
# 1. Create data view: store-logs-* with @timestamp
# 2. Go to Discover

# Reset only Filebeat + ES (keeps Grafana)
docker compose down
docker volume rm assignment-1_filebeat_data assignment-1_es_data
docker compose up --build
```

---

# Assignment 2 Progress (branch `assignment-2`)

## Part A — HTTP API (40) ✅
- [x] Redis as shared source of truth, business rules in `app/orders_service.py`
- [x] Routes cleaned up (plural nouns, PUT/PATCH/DELETE semantics), pydantic validation, RFC 9457 errors
- [x] `openapi.yaml` + drift test + contract test (`tests/test_contract.py`)
- [x] Idempotency-Key on POST /orders (works across HTTP and gRPC)
- [x] Rate limit POST /orders: 5 / 10 s / client IP, 429 + Retry-After
- [x] Python caller with timeout, retry+backoff, circuit breaker (`clients/`)
- [x] Evidence: `evidence/reliability_demo.txt`, `evidence/rate_limit_demo.txt`

## Part B — gRPC (30) ✅
- [x] `app/proto/orders.proto`, generated `app/gen/`, `grpc-server` container
- [x] `clients/grpc_client.py` with deadline + request id
- [x] Request id propagated via metadata; grpc.log shipped by Filebeat
- [x] Evidence: `evidence/examples.txt` (both transports, ES trace)

## Part C — Metrics (30) ✅ (screenshots pending)
- [x] gRPC interceptor metrics; Prometheus job `store-grpc`
- [x] Grafana provisioned: `grafana/dashboards/store-a2.json`
- [x] `scripts/gen_calls.sh` to populate panels
- [ ] Screenshots: Grafana A2 dashboard, Prometheus targets, Kibana trace, Swagger UI
