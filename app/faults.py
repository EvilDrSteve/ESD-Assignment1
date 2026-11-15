"""Temporary faults for the reliability demo (A2) and the slow-replica experiment (A3).

A fault is a Redis key with a TTL, so it switches itself off after the chosen
duration without anyone having to call "off". Every replica checks two keys:

  fault:<replica>   e.g. fault:B, only that replica (A3)
  fault:all         every replica (what A2's single key did)

The replica-specific key wins if both are set. Any replica can store a fault
for any other (shared Redis), so the switch works through Envoy no matter
which replica answers the admin call.

Modes (HTTP and gRPC; health checks, /metrics and the admin call are exempt):
  error : answer 503 / UNAVAILABLE without running the handler
  slow  : run the handler, then hold the response (default SLOW_DELAY_S),
          longer than the caller's timeout. The work is done but the caller
          doesn't know, which is exactly when idempotency matters (A2).
  delay : wait delay_ms (default 200) before running the handler. The replica
          stays healthy, just slower: the least-request experiment (A3).
"""

import json
import os
from dataclasses import dataclass

import redis

from replica import REPLICA
from store import store

ALL = "all"
MODES = ("error", "slow", "delay")
SLOW_DELAY_S = float(os.environ.get("FAULT_SLOW_DELAY_S", "3"))
DEFAULT_DELAY_MS = 200
ADMIN_ENABLED = os.environ.get("ENABLE_FAULT_ADMIN", "false").lower() == "true"


@dataclass(frozen=True)
class Fault:
    mode: str
    delay_s: float  # for slow and delay; 0 for error


def key(target: str) -> str:
    return f"fault:{target}"


def set_fault(mode: str, duration_s: float, target: str = ALL, delay_ms: int | None = None) -> Fault | None:
    if mode == "off":
        store.r.delete(key(target))
        return None
    if delay_ms is None:
        delay_ms = int(SLOW_DELAY_S * 1000) if mode == "slow" else DEFAULT_DELAY_MS
    fault = Fault(mode, 0 if mode == "error" else delay_ms / 1000)
    store.r.set(key(target), json.dumps({"mode": fault.mode, "delay_s": fault.delay_s}),
                px=int(duration_s * 1000))
    return fault


def current_fault(replica: str = REPLICA) -> Fault | None:
    """The fault this replica should apply now, if any."""
    try:
        own, everyone = store.r.mget(key(replica), key(ALL))
    except redis.RedisError:
        return None  # can't tell; the handler will report Redis being down itself
    raw = own or everyone
    if not raw:
        return None
    data = json.loads(raw)
    return Fault(data["mode"], float(data["delay_s"]))


def remaining_ms(target: str = ALL) -> int:
    return max(0, store.r.pttl(key(target)))
