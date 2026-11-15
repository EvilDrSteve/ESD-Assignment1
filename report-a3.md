# Assignment 3: Load Balancing — Report

**Enterprise Software Development, Fall 2026**

Three replicas (A, B, C) of the store behind Envoy, observed with the A1/A2
stack (Prometheus, Grafana, Elasticsearch/Kibana). Setup and commands are in
the README.

---

## Part A: Replicas, routing and shared state

*To be written in #16 (architecture diagram, shared state).*

## Part B: Baseline — everything to A

**Setup.** Envoy's `store_http` and `store_grpc` clusters each list only
replica A (`envoy/envoy.yaml`). B and C run but get no traffic. Prometheus
scrapes A, B, C (`assignment-1-app-N:8050/metrics`) and Envoy
(`envoy:9901/stats/prometheus`) directly, never through Envoy.

**Workload (the same for every later run).**

```bash
python clients/loadgen.py --interface both --rate 50 --duration 60 --label baseline-a-only
```

The workload is open loop: 50 calls/s of HTTP `GET /products/{id}` and
50 calls/s of gRPC `OrderService/GetProduct`, at the same time, for 60 s.
HTTP and gRPC are reported separately. The per-call timeout is 2 s, with no
retries. The run was 2026-11-15 15:36:30 to 15:37:30 UTC; the result is in
`evidence/a3/runs/baseline-a-only.json`.

**Prediction.** A answers 100% of both interfaces, B and C 0%, with no
errors.

**Client results**

| Interface | Target / actual rps | Sent | Successes | Failures | A / B / C share | Client p50 | Client p95 | Client p99 |
|---|---|---|---|---|---|---|---|---|
| HTTP | 50 / 50.02 | 3000 | 3000 | 0 (0%) | 100 / 0 / 0 % | 5.84 ms | 9.34 ms | 12.1 ms |
| gRPC | 50 / 50.02 | 3000 | 3000 | 0 (0%) | 100 / 0 / 0 % | 3.79 ms | 7.08 ms | 9.51 ms |

The share comes from the `X-Replica` header / `x-replica` metadata that each
replica adds to its responses.

**Dashboard** (Store A3, time range set to the run, `$window` = 30s):

- **Share over the run:** A 100%, B 0%, C 0% for both HTTP and gRPC
  (`screenshots/a3/11-grafana-share.jpg`).
- **Rate per replica:** A's line rises to a flat 50 req/s for HTTP and for
  gRPC. B and C have no application traffic.
  - The ramp up and down at the edges is the 30 s rate window, not the client.
  - Screenshots: `11-grafana-http.jpg`, `11-grafana-grpc.jpg`.
- **Active requests on A:** 0 at every 2 s sample. Each call takes about 1–2 ms
  on the replica, so at 50/s the in-flight count is about 0.1 and almost never
  caught by a sample.
- **Server-side latency (A):**
  - HTTP p50 about 1.4 ms, p95 about 3.6 ms
  - gRPC p50 about 1.3 ms, p95 about 2.4 ms
  - The rest of the client's p95 is Envoy, the Docker network, and the client
    itself.
- **Errors:** no 4xx/5xx and no non-OK gRPC codes.
- **Envoy:** each cluster sends 50 req/s upstream and has 1 healthy host out of
  1 (`11-grafana-envoy.jpg`).
- **Prometheus:** all five targets are UP: A, B, C, Envoy and node-exporter
  (`11-prometheus-targets.jpg`).

**Logs (Kibana).** Envoy's JSON access log and the replicas' logs both reach
`store-logs-*` through Filebeat and share the `request_id`.

| Search (time range = the run) | Result |
|---|---|
| `request_id.keyword : "lg-baseline-a-only-h-0"` | 2 events: Envoy (`upstream_cluster: store_http`, `upstream_host: 172.18.0.11:8050`, `replica: A`, `response_code: 200`) and replica A (`endpoint: /products/{product_id}`, `status: 200`) — `11-kibana-http-trace.jpg` |
| `request_id.keyword : "lg-baseline-a-only-g-0"` | 2 events: Envoy (`path: /store.v1.OrderService/GetProduct`, `upstream_host: …:50051`, `grpc_status: OK`, `replica: A`) and replica A (`rpc_method`, `grpc_code: OK`) — `11-kibana-grpc-trace.jpg` |
| `request_id.keyword : lg-baseline-a-only-*`, broken down by `replica` | 12,000 events (6,000 Envoy + 6,000 replica); every one has `replica: A` — `11-kibana-all-calls.jpg` |

An Elasticsearch aggregation over the same range confirms the split:
- `log_source: app`: 6000 events, all from A
- `log_source: envoy`: 6000 events, all with replica A

The request id is only ever a log field, never a metric label. Metric labels
are limited to replica (3 values), endpoint template, method, status and gRPC
method/code.

**Explanation.** The result matches the prediction. With one host in each
cluster, Envoy's round-robin policy has a single choice. B and C are
healthy, scraped and idle. The 30 s rate window explains the shape of the
graphs. HTTP and gRPC behave the same here; they only differ once there is a
choice to make (Part C).

**Note on measurement noise.** The first attempt at this run (15:24:57 UTC,
kept as `evidence/a3/runs/baseline-a-only-host-stall.json`) lost 8.5% of HTTP
and 7.2% of gRPC calls to the 2 s timeout.
- **What A's log shows:** replica A did nothing for about 6 s
  (15:25:15–15:25:21). Requests that arrived then were logged with
  durations of about 6000 ms.
- **The pause was not in the store:**
  - Redis, B and C were unaffected.
  - The load client's own send lag (200 ms, against under 20 ms in the clean
    runs) shows that the client, a separate process in the same WSL VM, was
    paused as well.
- **Retries:** two repeats of the same workload ran clean. The official run had
  no failures and a client p99 of 12 ms.
- **Takeaway:** for the experiments, a single bad run on this laptop is not
  evidence. Each result is checked against the client's send lag and against
  replicas that should be unaffected.

## Part C: Round robin

*#12*

## Part D: Experiments

*#13 (weighted), #14 (least request), #15 (failure and recovery)*

## Results table

| Run / interface | Policy and fault | Target / actual rps | A / B / C share | Client p95 | Error rate | Explanation |
|---|---|---|---|---|---|---|
| Baseline / HTTP | A only, no fault | 50 / 50.02 | 100 / 0 / 0 % | 9.34 ms | 0% | One host in the cluster |
| Baseline / gRPC | A only, no fault | 50 / 50.02 | 100 / 0 / 0 % | 7.08 ms | 0% | One host in the cluster |

## Conclusions

*#16*
