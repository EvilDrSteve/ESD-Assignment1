"""Fixed-window rate limiter, kept in process memory (single instance only).

Each client IP gets `limit` calls per `window_s`-second window. The window
starts at the client's first call; once it is used up, calls get 429 until
the window ends, and Retry-After says how many seconds that is.
"""

import math
import threading
import time


class RateLimited(Exception):
    def __init__(self, limit: int, window_s: int, retry_after: int):
        super().__init__(f"Rate limit of {limit} requests per {window_s}s exceeded")
        self.limit = limit
        self.window_s = window_s
        self.retry_after = retry_after


class FixedWindowLimiter:
    def __init__(self, limit: int, window_s: int, clock=time.monotonic):
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._windows: dict[str, tuple[float, int]] = {}  # client -> (window_start, count)
        self._lock = threading.Lock()

    def hit(self, client: str) -> int:
        """Count one call. Returns remaining calls, or raises RateLimited."""
        now = self.clock()
        with self._lock:
            start, count = self._windows.get(client, (now, 0))
            if now - start >= self.window_s:
                start, count = now, 0
            if count >= self.limit:
                retry_after = max(1, math.ceil(start + self.window_s - now))
                raise RateLimited(self.limit, self.window_s, retry_after)
            self._windows[client] = (start, count + 1)
            return self.limit - count - 1

    def reset(self):
        with self._lock:
            self._windows.clear()
