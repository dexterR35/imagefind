import threading
import time
from collections import deque
from collections.abc import Callable


class SlidingWindowRateLimiter:
    """Small in-process limiter for one API process.

    Returns no delay when a request is accepted, otherwise the number of
    seconds until the oldest request leaves the window.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit = max(1, limit)
        self.window_seconds = max(0.1, window_seconds)
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float | None:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return max(0.001, self.window_seconds - (now - bucket[0]))
            bucket.append(now)

            # Bound stale client metadata without a separate cleanup thread.
            if len(self._buckets) > 4096:
                for old_key in list(self._buckets):
                    old_bucket = self._buckets[old_key]
                    while old_bucket and old_bucket[0] <= cutoff:
                        old_bucket.popleft()
                    if not old_bucket:
                        del self._buckets[old_key]
            return None
