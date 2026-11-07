"""Per-user limits on AI calls, so one user (or a stolen token) can't run up the bill.

In-memory, so each server process counts on its own: with N ECS tasks a user
can make up to N x the limit. Good enough to stop abuse; for exact limits move
the counters to Redis (host-backend already depends on redis).
"""
import time
from collections import deque

from fastapi import HTTPException, status


class RateLimiter:
    def __init__(self):
        self.hits: dict[str, deque[float]] = {}

    def check(self, key: str, per_minute: int, per_day: int) -> None:
        now = time.time()
        window = self.hits.setdefault(key, deque())
        while window and now - window[0] > 86400:
            window.popleft()
        last_minute = sum(1 for t in window if now - t < 60)
        if last_minute >= per_minute or len(window) >= per_day:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many food scans. Please wait a little and try again.",
            )
        window.append(now)


limiter = RateLimiter()
