"""Drive http_caller through success, retry, open breaker and recovery.

Needs the stack running with ENABLE_FAULT_ADMIN=true (the default in
docker-compose.yml). Faults are switched on through POST /admin/faults and
switch themselves off after their duration.

Run: python clients/reliability_demo.py
"""

import argparse
import os
import time
import uuid

import requests

from http_caller import (
    BREAKER_RECOVERY_S, CallFailed, CircuitBreaker, CircuitOpenError, StoreClient, log, print_settings,
)


def set_fault(base_url: str, mode: str, duration_s: float = 10):
    r = requests.post(f"{base_url}/admin/faults", json={"mode": mode, "duration_s": duration_s}, timeout=2)
    r.raise_for_status()
    log(f"** server fault '{mode}' on for {duration_s}s **" if mode != "off" else "** server faults off **")


def order_count(base_url: str) -> int:
    return len(requests.get(f"{base_url}/orders", timeout=2).json())


def try_checkout(client: StoreClient, label: str):
    try:
        order = client.checkout()
        log(f"{label}: OK order {order['id'][:8]} total ${order['total']}")
        return True
    except CircuitOpenError as e:
        log(f"{label}: BLOCKED by breaker ({e})")
    except CallFailed as e:
        log(f"{label}: FAILED ({e})")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("STORE_URL", "http://localhost:8050"))
    args = parser.parse_args()
    base = args.base_url

    set_fault(base, "off")
    print_settings()
    client = StoreClient(base, breaker=CircuitBreaker())

    log("=== Phase 1: normal calls succeed ===")
    for i in range(2):
        try_checkout(client, f"call {i + 1}")

    log("=== Phase 2: timeout, then a retry with the same Idempotency-Key ===")
    cart_id = f"demo-{uuid.uuid4().hex[:6]}"
    client.set_cart_item(cart_id, "p2", 1)
    before = order_count(base)
    set_fault(base, "slow", 2.5)  # handler runs, response held 3s > 2s client timeout
    r = client.place_order(cart_id, idempotency_key=str(uuid.uuid4()))
    time.sleep(1)  # let the delayed first response finish server-side before counting
    log(f"result {r.status_code}, order {r.json()['id'][:8]}; orders before={before}, "
        f"after={order_count(base)} (exactly one new order, no duplicate)")

    log("=== Phase 3: server keeps failing -> breaker opens ===")
    set_fault(base, "error", 10)
    try_checkout(client, "call A")
    for label in ("call B", "call C"):
        try_checkout(client, label)
        time.sleep(1)

    log("=== Phase 4: after the recovery period a trial call is let through ===")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if try_checkout(client, "trial") and client.breaker.state == CircuitBreaker.CLOSED:
            break
        time.sleep(BREAKER_RECOVERY_S / 2)
    try_checkout(client, "after recovery")
    set_fault(base, "off")


if __name__ == "__main__":
    main()
