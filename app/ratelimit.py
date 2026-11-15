"""Fixed-window rate limiter, shared by all replicas through Redis (A3).

Each client IP gets `limit` calls per `window_s`-second window. The window
starts at the client's first call; once it is used up, calls get 429 until
the window ends, and Retry-After says how many seconds that is.

A2 kept the counters in process memory. With three replicas behind a round
robin balancer that gives every client three separate budgets (~3x the
limit), so the counter now lives in one Redis key per client:

    ratelimit:orders:<client ip>   value = calls so far, TTL = rest of the window

One MULTI/EXEC transaction per call, so concurrent calls on different
replicas can't both slip through:
    SET key 0 EX window NX   first call of a window creates the key and its expiry
    INCR key                 count this call
    PTTL key                 time left in the window, for Retry-After
"""

import math

from store import store


class RateLimited(Exception):
    def __init__(self, limit: int, window_s: int, retry_after: int):
        super().__init__(f"Rate limit of {limit} requests per {window_s}s exceeded")
        self.limit = limit
        self.window_s = window_s
        self.retry_after = retry_after


class FixedWindowLimiter:
    def __init__(self, limit: int, window_s: int, name: str = "orders", redis_client=None):
        self.limit = limit
        self.window_s = window_s
        self.prefix = f"ratelimit:{name}"
        self._redis = redis_client  # None: use the store's Redis (looked up per call)

    @property
    def redis(self):
        return self._redis or store.r

    def key(self, client: str) -> str:
        return f"{self.prefix}:{client}"

    def hit(self, client: str) -> int:
        """Count one call. Returns remaining calls, or raises RateLimited."""
        key = self.key(client)
        pipe = self.redis.pipeline(transaction=True)
        pipe.set(key, 0, ex=self.window_s, nx=True)
        pipe.incr(key)
        pipe.pttl(key)
        _, count, ttl_ms = pipe.execute()
        if count > self.limit:
            raise RateLimited(self.limit, self.window_s, max(1, math.ceil(ttl_ms / 1000)))
        return self.limit - count
