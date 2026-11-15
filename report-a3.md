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

## Part C: Round robin across A, B, C

**Setup.** Both Envoy clusters now list A, B and C, each with
`load_balancing_weight: 1`, and use `lb_policy: ROUND_ROBIN`.
- Both listeners are HTTP connection managers. On the gRPC side that means
  Envoy terminates HTTP/2 and picks a replica **per call**, not per
  connection.
- Upstream, `store_grpc` speaks HTTP/2 and `store_http` speaks HTTP/1.1.
- The workload is unchanged from Part B (`--label rr-equal`, run 16:08:32 to
  16:09:32 UTC, result in `evidence/a3/runs/rr-equal.json`). This is the
  starting point for every Part D run.

**Prediction.** About 33% each on both interfaces, no errors, and per-replica
rates of about 16.7/s instead of 50/s on A.

**Client results**

| Interface | Actual rps | Sent | Failures | A / B / C calls | A / B / C share | Client p95 (A-only → RR) |
|---|---|---|---|---|---|---|
| HTTP | 50.02 | 3000 | 0 | 1000 / 999 / 1001 | 33.3 / 33.3 / 33.4 % | 9.34 → 8.51 ms |
| gRPC | 50.02 | 3000 | 0 | 1000 / 1000 / 1000 | 33.3 / 33.3 / 33.3 % | 7.08 → 5.88 ms |

**Checked against Envoy's access log** (the log Envoy wrote and the same
records in Elasticsearch, `12-kibana-envoy-by-replica.jpg`):
- `store_grpc`: A 1000, B 1000, C 1000.
- `store_http`: A 1000, B 999, C 1001.
- The replicas' own logs for the run: A 2000, B 1999, C 2001.

The client, Envoy and the replicas agree call for call.

**Dashboard** (`12-grafana-share-http.jpg`, `12-grafana-grpc.jpg`,
`12-grafana-envoy.jpg`):
- **Rates:** the three lines sit on top of each other at about 16.7 req/s per
  interface, and the dashed total line is at 50.
- **Share over the run:** HTTP 33.6 / 33.3 / 33.1 %, gRPC 34.0 / 33.3 /
  32.7 %. These are close to the exact counts but not equal to them.
  - `increase()` estimates each counter's growth from 2 s samples and
    extrapolates to the edges of the time range.
  - The three replicas are scraped at slightly different moments, so the edges
    cut each one differently.
  - So the client and log counts are the exact numbers; the dashboard is right
    to about ±1%.
- **Server-side latency:** p50 1.3 ms and p95 about 2.4 ms on every replica,
  the same as A alone in Part B.
- **Errors and health:** no errors; Envoy shows 3 of 3 hosts healthy in each
  cluster.

**Why gRPC is exact and HTTP is almost exact.** Envoy runs one worker thread
per core (16 here), and every worker keeps its **own** round-robin position.

*gRPC:*
- The client uses one channel, which is one HTTP/2 connection
  (`http.grpc.downstream_cx_total = 1`), so a single worker handles every
  call.
- That worker rotates strictly A → B → C. Over 3000 calls, no replica was
  ever picked twice in a row.
- The worker holds one multiplexed HTTP/2 connection to each replica
  (`cluster.store_grpc.upstream_cx_total = 3`).

*HTTP:*
- The client spreads its calls over a pool of HTTP/1.1 connections. Envoy
  saw about 10 from the load client (16 downstream connections in total,
  including earlier `curl` tests).
- Those connections land on different workers, each rotating on its own,
  and Envoy opened 28 upstream connections.
- The interleaved rotations still add up to an even split, but not a strict
  sequence: a replica was picked twice in a row 4 times in 3000 calls.

**HTTP/2 matters for gRPC.** If the gRPC listener were a TCP proxy, Envoy would
choose a replica once, for the client's single connection, and all 3000 calls
would go to that replica. Per-call balancing needs a listener that understands
HTTP/2 streams.

**Compared with Part B.**
- **Load:** each replica now does a third of the work.
- **Latency:** the client p95 improved a little (HTTP −0.8 ms, gRPC −1.2 ms).
  At 50 calls/s, A alone was nowhere near saturation (in-flight about 0.1), so
  spreading the load barely changes latency; most of the client's time is
  Envoy and the network, not queueing on a replica.
- **When it would matter:** the benefit of round robin shows up when one
  replica is slow, overloaded or down, which is what Part D tests.
- **Noise:** one brief host pause appears here too (client max 410 ms, a spike
  of 7 in-flight on A at 16:09:07). It is too short to change the shares or
  p95.

## Part D: Experiments

*#13 (weighted), #14 (least request), #15 (failure and recovery)*

## Results table

| Run / interface | Policy and fault | Target / actual rps | A / B / C share | Client p95 | Error rate | Explanation |
|---|---|---|---|---|---|---|
| Baseline / HTTP | A only, no fault | 50 / 50.02 | 100 / 0 / 0 % | 9.34 ms | 0% | One host in the cluster |
| Baseline / gRPC | A only, no fault | 50 / 50.02 | 100 / 0 / 0 % | 7.08 ms | 0% | One host in the cluster |
| RR / HTTP | Round robin 1:1:1, no fault | 50 / 50.02 | 33.3 / 33.3 / 33.4 % | 8.51 ms | 0% | Per-worker rotation over ~10 client connections, near-even |
| RR / gRPC | Round robin 1:1:1, no fault | 50 / 50.02 | 33.3 / 33.3 / 33.3 % | 5.88 ms | 0% | One HTTP/2 connection, one worker, strict A→B→C per call |

## Conclusions

*#16*
