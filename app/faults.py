"""Temporary faults for the reliability demo (Part A caller).

A fault is a Redis key with a TTL, so it switches itself off after the chosen
duration without anyone having to call "off".

  error : answer 503 without running the handler (server failure)
  slow  : run the handler, then hold the response for SLOW_DELAY_S seconds,
          longer than the caller's timeout. The work is done, but the caller
          doesn't know, which is exactly when idempotency matters.
"""

import os

from store import store

FAULT_KEY = "fault:http"
MODES = ("error", "slow")
SLOW_DELAY_S = float(os.environ.get("FAULT_SLOW_DELAY_S", "3"))
ADMIN_ENABLED = os.environ.get("ENABLE_FAULT_ADMIN", "false").lower() == "true"


def set_fault(mode: str, duration_s: float):
    if mode == "off":
        store.r.delete(FAULT_KEY)
    else:
        store.r.set(FAULT_KEY, mode, px=int(duration_s * 1000))


def current_fault() -> str | None:
    return store.r.get(FAULT_KEY)


def remaining_ms() -> int:
    return max(0, store.r.pttl(FAULT_KEY))
