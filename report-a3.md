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

Every experiment starts from the Part C config, uses the same workload, and
puts the base config back afterwards. HTTP and gRPC are reported separately,
with no client retries and no circuit breaker. Each experiment's Envoy config
is generated from `envoy/envoy.yaml` by `scripts/build_envoy_variants.py`, so
it differs from the base only in the lines it is about. Compose mounts it
through `ENVOY_CONFIG`.

### D1. Weighted round robin, A:B:C = 2:1:1

**Change.** `load_balancing_weight: 2` for A in both clusters; B and C stay at
1 (`envoy/weighted-2-1-1.yaml`).

```bash
ENVOY_CONFIG=weighted-2-1-1.yaml docker compose up -d --force-recreate envoy
python clients/loadgen.py --interface both --rate 50 --duration 60 --label wrr-2-1-1
docker compose up -d --force-recreate envoy      # restore 1:1:1
```

The run was 16:17:45 to 16:18:45 UTC, with the result in
`evidence/a3/runs/wrr-2-1-1.json`.

**Prediction** (written before the run):
- **Shares:** 50 / 25 / 25 % on both interfaces, with gRPC exact and HTTP
  within a call or two.
- **Rates:** A at about 25/s per interface, B and C at about 12.5/s.
- **Latency:** per-replica latency the same as in Part C. A weight decides
  how many calls a replica gets, not how fast it serves them, so it adds no
  capacity.
- **Active requests:** about 2× as many on A as on B or C, but still close to
  zero at this load.
- **Errors:** none.

**Results**

| Interface | A / B / C calls (client) | A / B / C (Envoy log) | Share | Failures | Client p95 (RR → 2:1:1) |
|---|---|---|---|---|---|
| HTTP | 1499 / 751 / 750 | 1499 / 751 / 750 | 50.0 / 25.0 / 25.0 % | 0 | 8.51 → 8.01 ms |
| gRPC | 1500 / 750 / 750 | 1500 / 750 / 750 | 50.0 / 25.0 / 25.0 % | 0 | 5.88 → 5.24 ms |

**Observations** (screenshots `13-grafana-share-http.jpg`,
`13-grafana-grpc.jpg`, `13-grafana-envoy.jpg`,
`13-kibana-envoy-by-replica.jpg`):
- **Shares:** the share bars read 50.0 / 25.0 / 25.0 % on both interfaces.
- **Rates:** A's line plateaus at 25 req/s, and B's and C's overlap at
  12.5 req/s.
- **Envoy's order:** Envoy picks in the repeating pattern A A B C (from the
  access log, by request number):

  ```
  gRPC: AABCAABCAABCAABCAABC…
  HTTP: ABBCAABCAABCAABCAABC…
  ```

- **Server-side latency:** the same on every replica and the same as in
  Part C:
  - HTTP p50 1.27–1.29 ms, p95 about 2.4 ms
  - gRPC p50 1.25 ms, p95 about 2.4 ms
- **Active requests:** 0 on every 2 s sample, for all three replicas.
- **Health:** no errors; Envoy shows 3 of 3 hosts healthy.

**Explanation**
- **Shares:** they match the prediction exactly. With weights, each Envoy
  worker sends each replica calls in proportion to its weight, which gives
  the A A B C cycle. gRPC is exact for the same reason as in Part C: one
  client connection, so one worker and one sequence.
- **HTTP:** it starts slightly off (`ABBC…`) because two client connections
  were briefly on different workers. The total is still within one call of
  50/25/25.
- **Latency:** it did not change.
  - The weight decides how many calls A gets, not how fast A serves them.
  - Each call still costs A about 1.3 ms, and at 25 calls/s A is busy only
    about 3% of the time, so nothing queues.
  - The client p95 differences between the runs (≤0.6 ms) are within the
    noise of this laptop.
- **Active requests:** by Little's law, in-flight = rate × time per call.
  - A: 25/s × ~1.3 ms ≈ 0.03 in flight.
  - B and C: ≈ 0.016 each.
  - So A really does hold twice as many calls in flight, but a gauge sampled
    every 2 s almost never catches a call.
- **What a higher weight is not:** it is only a promise that the replica can
  take more. If A were really the same size as B and C and the load were near
  capacity, 2:1:1 would push A into queueing first. Its latency and active
  requests would rise, while B and C sat partly idle. Weights belong on
  replicas that really are bigger (more CPU, faster hardware). Least request
  (D2) instead adapts to how busy each replica actually is.

**Restore.** I recreated Envoy without `ENVOY_CONFIG`; the admin `/clusters`
endpoint shows `weight::1` for every host again.

### D2. Slow replica B: round robin vs least request

**Change.**
- **Fault:** a 200 ms delay on every call to B, both interfaces, via the
  fault switch (`bash scripts/fault.sh B delay 200 900`). Health checks and
  `/metrics` are exempt. Weights stay equal.
- **Two runs, same workload:**
  - `delayb-rr`: base config, `ROUND_ROBIN`, 16:27:01 to 16:28:01 UTC
  - `delayb-lr`: `envoy/least-request.yaml`, `LEAST_REQUEST`, 16:28:35 to
    16:29:35 UTC
- **Least request with equal weights:** Envoy picks two hosts at random (with
  replacement, `choice_count` 2) and sends the request to the one with fewer
  active requests ("power of two choices").

```bash
bash scripts/fault.sh B delay 200 900
python clients/loadgen.py --interface both --rate 50 --duration 60 --label delayb-rr
ENVOY_CONFIG=least-request.yaml docker compose up -d --force-recreate envoy
python clients/loadgen.py --interface both --rate 50 --duration 60 --label delayb-lr
docker compose up -d --force-recreate envoy && bash scripts/fault.sh B off      # restore
```

**Prediction** (written before the runs):

*Round robin:*
- **B's share:** still about 33%, because round robin doesn't look at load.
- **B's active requests:** about 16.7/s × 0.2 s ≈ 3.3 per interface.
- **Client p95:** about 200 ms, since a third of all calls are slow.

*Least request:*
- **B's share:** B almost always has calls in flight while A and C have none,
  so it loses nearly every comparison. B still gets every call where both
  random picks are B (1/9 ≈ 11%), plus a few ties. Expected roughly 5–15%.
- **B's active requests:** they fall in proportion to its share.
- **Client p95:** it stays near 200 ms unless B's share falls below 5%.

**Results**

| Run / interface | A / B / C calls | B share | Avg in flight on B | Client p50 | Client p95 | Calls slower than 200 ms |
|---|---|---|---|---|---|---|
| RR / HTTP | 999 / 1002 / 999 | 33.4 % | 3.31 | 6.05 ms | 206.99 ms | 33.4 % |
| RR / gRPC | 1000 / 1000 / 1000 | 33.3 % | 3.48 | 4.02 ms | 204.56 ms | 33.3 % |
| LR / HTTP | 1285 / 445 / 1270 | 14.8 % | 1.59 | 6.17 ms | 207.01 ms | 14.8 % |
| LR / gRPC | 1258 / 426 / 1316 | 14.2 % | 1.69 | 3.95 ms | 204.51 ms | 14.2 % |

Notes on the table:
- **Call counts:** for both runs they match Envoy's access log in
  Elasticsearch exactly.
- **Avg in flight on B:** the mean of the replica's in-flight gauge over the
  run (`avg_over_time(...[58s:2s])`).
- **No errors:** there were no failures in either run.
- **Latency by replica:** the client splits latency by the replica that
  answered.
  - B: p50 about 205 ms in both runs.
  - A and C: p50 about 4–6 ms, p95 under 9 ms in both runs.

**Observations** (`14-share-rr.png`, `14-share-lr.png`,
`14-grafana-http-rr-then-lr.jpg`, `14-grafana-grpc-rr-then-lr.jpg`,
`14-grafana-envoy.jpg`; the long screenshots show both runs side by side):

*Round robin:*
- B's rate line sits exactly on A's and C's (about 16.7/s each).
- B's active-requests line runs at 3–4, while A and C stay at 0.
- Envoy's active upstream requests per cluster run at 3–5.

*Least request:*
- B's rate drops to about 7/s, and A and C rise to about 21/s.
- B's active requests fall to 1–2, with gaps where it has none.

*Both runs:*
- The fault-switch tile shows B at 200 ms throughout.
- Grafana's server-side p50 for B reads 175 ms, not about 205 ms.
  `histogram_quantile` interpolates linearly inside the 100–250 ms bucket, so
  the client's per-replica numbers are the precise ones here.

**Explanation**
- **Round robin:** it hands B every third call whatever B's state, so B's
  share is unchanged.
  - B's in-flight count is what Little's law predicts:
    16.7/s × 0.205 s ≈ 3.4, measured 3.3–3.5.
  - A third of all calls take 205 ms, so the client p95 (and p99) are B's
    latency.
- **Least request:** it reacts to exactly that in-flight count. Each call
  compares two random hosts.
  - **When B loses:** B has 1–2 calls open almost all the time and A and C
    have about 0, so B loses whenever the pair includes A or C.
  - **When B is still chosen:** when both picks are B (1/9 = 11.1%), and
    rarely when B happens to have 0 in flight and ties.
  - **Measured:** 14.2–14.8%, just above the 11% floor, as predicted.
  - **Little's law again:** in-flight on B = 7.3/s × 0.205 s ≈ 1.5, measured
    1.6–1.7.
- **Client latency:** the share of slow calls fell from 33% to 14–15%, which
  roughly halves the average.
  - Average ≈ share × 205 ms + the rest × 5 ms: about 72 ms with round robin
    vs about 35 ms with least request (estimated from the per-replica p50s).
  - The p95 did **not** improve, because 14% slow calls is still more than
    5%, so the 95th percentile is still a B call. Every percentile above
    about the 86th still hits B; the improvement shows at p80–p85 and in the
    average.
- **The same on both interfaces:** least request counts active requests per
  host across Envoy's workers, so HTTP and gRPC react identically, despite
  the different connection setups.
- **Getting the p95 down:** raising `choice_count` to 3 would make the
  "both picks are B" floor 1/27 ≈ 4%, below 5%.
  - Removing B with outlier detection (on latency) would take it out
    completely.
  - The cost is that a merely slow replica loses almost all traffic.
- **Health checks can't help here:** a 200 ms delay doesn't fail them,
  because they are exempt from the fault and a slow replica still answers
  `/health` in time. D3 relies on that difference.

**Restore.** Envoy was recreated with the base config and the fault was
switched off. The fault-switch gauge reads 0 on A, B and C, and `LEAST_REQUEST`
no longer appears in the config dump.
- The first `fault.sh B off` failed: it ran while Envoy was still starting
  (curl exit 56, connection reset).
- B stayed delayed for about 20 s more, after the second run had ended, until
  it was sent again.
- `scripts/fault.sh` now retries its request.

## Results table

| Run / interface | Policy and fault | Target / actual rps | A / B / C share | Client p95 | Error rate | Explanation |
|---|---|---|---|---|---|---|
| Baseline / HTTP | A only, no fault | 50 / 50.02 | 100 / 0 / 0 % | 9.34 ms | 0% | One host in the cluster |
| Baseline / gRPC | A only, no fault | 50 / 50.02 | 100 / 0 / 0 % | 7.08 ms | 0% | One host in the cluster |
| RR / HTTP | Round robin 1:1:1, no fault | 50 / 50.02 | 33.3 / 33.3 / 33.4 % | 8.51 ms | 0% | Per-worker rotation over ~10 client connections, near-even |
| RR / gRPC | Round robin 1:1:1, no fault | 50 / 50.02 | 33.3 / 33.3 / 33.3 % | 5.88 ms | 0% | One HTTP/2 connection, one worker, strict A→B→C per call |
| WRR / HTTP | Weighted RR 2:1:1, no fault | 50 / 50.02 | 50.0 / 25.0 / 25.0 % | 8.01 ms | 0% | Weights set the share; latency unchanged (no extra capacity, load far below it) |
| WRR / gRPC | Weighted RR 2:1:1, no fault | 50 / 50.02 | 50.0 / 25.0 / 25.0 % | 5.24 ms | 0% | Exact A A B C cycle on the single connection |
| Slow B, RR / HTTP | RR 1:1:1, B +200 ms | 50 / 50.01 | 33.3 / 33.4 / 33.3 % | 206.99 ms | 0% | RR ignores load; 1/3 of calls slow |
| Slow B, RR / gRPC | RR 1:1:1, B +200 ms | 50 / 50.02 | 33.3 / 33.3 / 33.3 % | 204.56 ms | 0% | Same |
| Slow B, LR / HTTP | Least request, B +200 ms | 50 / 50.02 | 42.8 / 14.8 / 42.3 % | 207.01 ms | 0% | B loses most pairings; 1/9 floor keeps p95 on B |
| Slow B, LR / gRPC | Least request, B +200 ms | 50 / 50.02 | 41.9 / 14.2 / 43.9 % | 204.51 ms | 0% | Same; per-host active counts shared by workers |

## Conclusions

*#16*
