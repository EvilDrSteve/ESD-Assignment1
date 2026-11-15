"""Fixed-rate (open-loop) load client for HTTP and gRPC (A3).

Sends calls at a fixed rate for a fixed duration and never waits for one
response before sending the next. Call i is due at start + i / rate; each is
fired as its own asyncio task. A slow server therefore gets *more* calls in
flight, not fewer calls per second, which is what the load-balancing
experiments need (a closed loop would quietly lower the rate instead).

  HTTP: GET  /products/{id}                (through Envoy, localhost:8050)
  gRPC: store.v1.OrderService/GetProduct   (through Envoy, localhost:50051)

No retries and no circuit breaker. Every call has its own request id
(lg-<run>-<n>), so any call can be found in Kibana. Latency is measured from
the moment the call was *due*, so client-side lag counts against the result.
The answering replica is read from the X-Replica header / x-replica metadata.

Examples
  python clients/loadgen.py --interface http --rate 50 --duration 60
  python clients/loadgen.py --interface grpc --rate 50 --duration 60 --label rr-grpc
  python clients/loadgen.py --interface both --rate 50 --duration 60 --label baseline
"""

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "gen"))

import grpc  # noqa: E402
import httpx  # noqa: E402

import orders_pb2  # noqa: E402
import orders_pb2_grpc  # noqa: E402

PRODUCT_IDS = [f"p{n}" for n in range(1, 10)]  # the seeded catalogue
DEFAULT_TARGETS = {"http": "localhost:8050", "grpc": "localhost:50051"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


class Recorder:
    def __init__(self):
        self.results: list[tuple[str, str, float]] = []  # (status, replica, latency_ms)
        self.max_send_lag_ms = 0.0

    def add(self, status: str, replica: str | None, latency_ms: float):
        self.results.append((status, replica or "?", latency_ms))


async def call_http(client: httpx.AsyncClient, i: int, run: str, rec: Recorder, due: float):
    try:
        r = await client.get(f"/products/{PRODUCT_IDS[i % len(PRODUCT_IDS)]}",
                             headers={"X-Request-ID": f"lg-{run}-{i}"})
        status, replica = str(r.status_code), r.headers.get("x-replica")
    except httpx.TimeoutException:
        status, replica = "timeout", None
    except httpx.HTTPError as e:
        status, replica = type(e).__name__, None
    rec.add(status, replica, (time.perf_counter() - due) * 1000)


async def call_grpc(stub, i: int, run: str, rec: Recorder, due: float, timeout: float):
    call = stub.GetProduct(orders_pb2.GetProductRequest(product_id=PRODUCT_IDS[i % len(PRODUCT_IDS)]),
                           timeout=timeout, metadata=(("x-request-id", f"lg-{run}-{i}"),))
    replica = None
    try:
        await call
        status = "OK"
    except grpc.RpcError as e:
        status = e.code().name
    try:
        metadata = await call.initial_metadata()  # grpc.aio.Metadata: has .get(), not a dict
        replica = metadata.get("x-replica") if metadata else None
    except Exception:  # no headers at all (e.g. deadline hit before the replica answered)
        pass
    rec.add(status, replica, (time.perf_counter() - due) * 1000)


async def drive(interface: str, target: str, rate: float, duration: float, timeout: float, run: str) -> dict:
    total = int(rate * duration)
    rec = Recorder()
    tasks = []

    if interface == "http":
        # Plenty of connections so the client never queues calls behind each other
        client = httpx.AsyncClient(base_url=f"http://{target}", timeout=timeout,
                                   limits=httpx.Limits(max_connections=1000, max_keepalive_connections=1000))
        fire = lambda i, due: call_http(client, i, run, rec, due)  # noqa: E731
        close = client.aclose
    else:
        # One channel = one HTTP/2 connection to Envoy; Envoy balances per call
        channel = grpc.aio.insecure_channel(target, options=[("grpc.enable_retries", 0)])
        stub = orders_pb2_grpc.OrderServiceStub(channel)
        fire = lambda i, due: call_grpc(stub, i, run, rec, due, timeout)  # noqa: E731
        close = channel.close

    started_at = datetime.now(timezone.utc)
    start = time.perf_counter() + 0.05
    next_report = 10
    for i in range(total):
        due = start + i / rate
        wait = due - time.perf_counter()
        if wait > 0:
            await asyncio.sleep(wait)
        rec.max_send_lag_ms = max(rec.max_send_lag_ms, (time.perf_counter() - due) * 1000)
        tasks.append(asyncio.create_task(fire(i, due)))
        if i / rate >= next_report:
            print(f"  [{interface}] t={next_report:>4}s sent={i + 1} done={len(rec.results)}", flush=True)
            next_report += 10
    send_window = time.perf_counter() - start
    await asyncio.gather(*tasks)
    ended_at = datetime.now(timezone.utc)
    await close()

    statuses = Counter(s for s, _, _ in rec.results)
    ok = "OK" if interface == "grpc" else None
    successes = sum(n for s, n in statuses.items() if (s == ok if ok else s.startswith("2")))
    replicas = Counter(r for _, r, _ in rec.results)
    latencies = [ms for _, _, ms in rec.results]
    sent = len(rec.results)
    return {
        "interface": interface,
        "target": target,
        "operation": "GET /products/{id}" if interface == "http" else "OrderService/GetProduct",
        "target_rate": rate,
        "duration_s": duration,
        "started_at": started_at.isoformat(timespec="seconds"),
        "ended_at": ended_at.isoformat(timespec="seconds"),
        "sent": sent,
        "actual_rate": round(sent / send_window, 2),
        "max_send_lag_ms": round(rec.max_send_lag_ms, 2),
        "successes": successes,
        "failures": sent - successes,
        "error_rate_pct": round(100 * (sent - successes) / sent, 2) if sent else None,
        "by_status": dict(statuses.most_common()),
        "by_replica": dict(sorted(replicas.items())),
        "share_pct": {r: round(100 * n / sent, 1) for r, n in sorted(replicas.items())},
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "p99": round(percentile(latencies, 99), 2),
            "max": round(max(latencies), 2),
        },
    }


def print_summary(s: dict):
    lat = s["latency_ms"]
    print(f"\n{s['interface'].upper()} {s['operation']} via {s['target']}")
    print(f"  rate      target {s['target_rate']}/s, actual {s['actual_rate']}/s for {s['duration_s']} s "
          f"(max send lag {s['max_send_lag_ms']} ms)")
    print(f"  sent      {s['sent']}  successes {s['successes']}  failures {s['failures']} "
          f"({s['error_rate_pct']}%)  by status {s['by_status']}")
    print(f"  replicas  {s['by_replica']}  share % {s['share_pct']}")
    print(f"  latency   p50 {lat['p50']} ms  p95 {lat['p95']} ms  p99 {lat['p99']} ms  max {lat['max']} ms")


async def main_async(args) -> list[dict]:
    interfaces = ["http", "grpc"] if args.interface == "both" else [args.interface]
    run = re.sub(r"[^A-Za-z0-9._-]", "-", args.label)[:40]
    jobs = [drive(i, args.target or DEFAULT_TARGETS[i], args.rate, args.duration, args.timeout, f"{run}-{i[0]}")
            for i in interfaces]
    return await asyncio.gather(*jobs)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interface", choices=["http", "grpc", "both"], required=True,
                        help="both = HTTP and gRPC at the same time, each at --rate, reported separately")
    parser.add_argument("--rate", type=float, default=50, help="calls per second, per interface (default 50)")
    parser.add_argument("--duration", type=float, default=60, help="seconds of sending (default 60)")
    parser.add_argument("--target", default=None, help="host:port (default localhost:8050 / localhost:50051)")
    parser.add_argument("--timeout", type=float, default=2.0, help="per-call timeout in seconds (default 2)")
    parser.add_argument("--label", default=uuid.uuid4().hex[:8], help="run name, used in request ids and the file name")
    parser.add_argument("--out", default=os.path.join(ROOT, "evidence", "a3", "runs"),
                        help="folder for the JSON result (default evidence/a3/runs)")
    args = parser.parse_args()
    if args.interface == "both" and args.target:
        parser.error("--target needs a single --interface")

    print(f"run '{args.label}': {args.interface} at {args.rate}/s for {args.duration} s ...", flush=True)
    summaries = asyncio.run(main_async(args))
    for s in summaries:
        print_summary(s)

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"{args.label}.json")
    with open(path, "w") as f:
        json.dump({"label": args.label, "runs": summaries}, f, indent=2)
    print(f"\nsaved {path}")


if __name__ == "__main__":
    main()
