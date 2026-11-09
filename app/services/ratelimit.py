"""Per-user and per-IP limits on booking writes. The single SQLite writer is the real queue."""

from __future__ import annotations

import time
from collections import defaultdict

from app.config import RATE_LIMIT_IP, RATE_LIMIT_USER, RATE_WINDOW_SEC


class RateLimit:
    def __init__(self, limit: int, window: int = RATE_WINDOW_SEC):
        self.limit = limit
        self.window = window
        self.hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in self.hits[key] if now - t < self.window]
        if len(recent) >= self.limit:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        return True


user_limit = RateLimit(RATE_LIMIT_USER)
ip_limit = RateLimit(RATE_LIMIT_IP)
auth_limit = RateLimit(12, RATE_WINDOW_SEC)


def allow_auth(ip: str) -> bool:
    """Separate budget for sign-in and sign-up, so a login loop does not block booking."""
    return auth_limit.allow(ip or "local")


def check(user_id: int | None, ip: str) -> bool:
    if not ip_limit.allow(ip or "local"):
        return False
    if user_id is not None and not user_limit.allow(str(user_id)):
        return False
    return True
