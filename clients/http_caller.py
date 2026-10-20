"""A small "checkout service" that calls the store's HTTP API reliably.

Settings (also printed at start-up)
  timeout          2.0 s per attempt (connect + read)
  retries          max 3 attempts per call (1 try + 2 retries),
                   backoff 0.5 s, 1 s (doubling) + up to 0.1 s jitter
                   retried: timeouts, connection errors, 502/503/504, 429 (waits Retry-After)
                   never retried: other 4xx (the request itself is wrong)
                   only for safe operations: PUT cart item (sets a quantity) and
                   POST /orders *with* an Idempotency-Key
  circuit breaker  opens after 3 consecutive failed attempts, rejects calls
                   for 5 s, then lets 1 trial call through (half-open):
                   success -> closed, failure -> open again

Run: python clients/http_caller.py --orders 3
"""

import argparse
import os
import random
import time
import uuid

import requests

TIMEOUT_S = 2.0
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5
BACKOFF_JITTER_S = 0.1
MAX_RETRY_AFTER_S = 5
RETRYABLE_STATUS = {502, 503, 504}
BREAKER_FAILURE_THRESHOLD = 3
BREAKER_RECOVERY_S = 5.0

_T0 = time.monotonic()


def log(msg: str):
    print(f"[{time.monotonic() - _T0:6.2f}s] {msg}", flush=True)


class CircuitOpenError(Exception):
    pass


class CallFailed(Exception):
    pass


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "CLOSED", "OPEN", "HALF_OPEN"

    def __init__(self, failure_threshold=BREAKER_FAILURE_THRESHOLD, recovery_s=BREAKER_RECOVERY_S,
                 clock=time.monotonic, on_change=None):
        self.failure_threshold = failure_threshold
        self.recovery_s = recovery_s
        self.clock = clock
        self.on_change = on_change or (lambda old, new: log(f"breaker {old} -> {new}"))
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at = 0.0

    def _set(self, state):
        if state != self.state:
            old, self.state = self.state, state
            self.on_change(old, state)

    def before_call(self):
        """Raise CircuitOpenError if calls are blocked right now."""
        if self.state == self.OPEN:
            waited = self.clock() - self.opened_at
            if waited < self.recovery_s:
                raise CircuitOpenError(f"circuit open, next trial in {self.recovery_s - waited:.1f}s")
            self._set(self.HALF_OPEN)  # let exactly this one call through as a trial

    def record_success(self):
        self.failures = 0
        self._set(self.CLOSED)

    def record_failure(self):
        self.failures += 1
        if self.state == self.HALF_OPEN or self.failures >= self.failure_threshold:
            self.opened_at = self.clock()
            self._set(self.OPEN)


class StoreClient:
    def __init__(self, base_url: str, breaker: CircuitBreaker | None = None, timeout_s=TIMEOUT_S,
                 max_attempts=MAX_ATTEMPTS, sleep=time.sleep):
        self.base_url = base_url.rstrip("/")
        self.breaker = breaker or CircuitBreaker()
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.session = requests.Session()

    def _backoff(self, attempt: int) -> float:
        return BACKOFF_BASE_S * 2 ** (attempt - 1) + random.uniform(0, BACKOFF_JITTER_S)

    def _call(self, method: str, path: str, retry_safe: bool, **kwargs) -> requests.Response:
        """One logical call: up to max_attempts tries, all sharing one X-Request-ID."""
        headers = kwargs.pop("headers", {})
        headers.setdefault("X-Request-ID", f"caller-{uuid.uuid4().hex[:12]}")
        attempts = self.max_attempts if retry_safe else 1

        for attempt in range(1, attempts + 1):
            self.breaker.before_call()  # CircuitOpenError: give up now, don't touch the network
            what = f"{method} {path} attempt {attempt}/{attempts}"
            wait = None
            try:
                resp = self.session.request(method, self.base_url + path, headers=headers,
                                            timeout=self.timeout_s, **kwargs)
            except requests.Timeout:
                problem = f"timeout after {self.timeout_s}s"
            except requests.ConnectionError:
                problem = "connection error"
            else:
                if resp.status_code == 429:
                    # The server is healthy, just protecting itself: not a breaker failure
                    wait = min(int(resp.headers.get("Retry-After", "1")), MAX_RETRY_AFTER_S)
                    log(f"{what} -> 429, Retry-After {resp.headers.get('Retry-After')}s")
                    if attempt == attempts:
                        return resp
                    self.sleep(wait)
                    continue
                if resp.status_code not in RETRYABLE_STATUS:
                    replay = " (idempotent replay)" if resp.headers.get("Idempotent-Replayed") else ""
                    log(f"{what} -> {resp.status_code}{replay}")
                    self.breaker.record_success()
                    return resp
                problem = f"HTTP {resp.status_code}"

            last = attempt == attempts
            backoff = self._backoff(attempt)
            log(f"{what} -> {problem}; " + ("giving up" if last else f"retrying in {backoff:.2f}s"))
            self.breaker.record_failure()
            if last:
                raise CallFailed(f"{method} {path}: {problem}")
            self.sleep(backoff)
        raise CallFailed(f"{method} {path}: no attempts left")

    # ── API operations ────────────────────────────────────────────────────

    def set_cart_item(self, cart_id: str, product_id: str, quantity: int) -> dict:
        # PUT sets the quantity, so repeating it is safe
        r = self._call("PUT", f"/carts/{cart_id}/items/{product_id}", retry_safe=True,
                       json={"quantity": quantity})
        r.raise_for_status()
        return r.json()

    def place_order(self, cart_id: str, idempotency_key: str | None) -> requests.Response:
        # Only retried when an Idempotency-Key protects against duplicate orders
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self._call("POST", "/orders", retry_safe=idempotency_key is not None,
                          json={"cart_id": cart_id}, headers=headers)

    def checkout(self, product_id="p1", quantity=1) -> dict:
        cart_id = f"caller-{uuid.uuid4().hex[:8]}"
        self.set_cart_item(cart_id, product_id, quantity)
        r = self.place_order(cart_id, idempotency_key=str(uuid.uuid4()))
        r.raise_for_status()
        return r.json()


def print_settings():
    log(f"settings: timeout={TIMEOUT_S}s, max_attempts={MAX_ATTEMPTS}, backoff={BACKOFF_BASE_S}s x2 "
        f"(+<= {BACKOFF_JITTER_S}s jitter), breaker threshold={BREAKER_FAILURE_THRESHOLD} failures, "
        f"recovery={BREAKER_RECOVERY_S}s")


def main():
    parser = argparse.ArgumentParser(description="Reliable caller for POST /orders")
    parser.add_argument("--base-url", default=os.environ.get("STORE_URL", "http://localhost:8050"))
    parser.add_argument("--orders", type=int, default=3)
    args = parser.parse_args()

    print_settings()
    client = StoreClient(args.base_url)
    for i in range(args.orders):
        try:
            order = client.checkout()
            log(f"checkout {i + 1}: order {order['id']} total ${order['total']}")
        except (CallFailed, CircuitOpenError, requests.HTTPError) as e:
            log(f"checkout {i + 1} failed: {e}")


if __name__ == "__main__":
    main()
