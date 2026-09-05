"""
Rate limiting and cost protection.

Requests are counted per tenant and client IP in a sliding window. The
limiter is in-memory and per process: adequate for abuse protection, not for
billing-grade quotas.
"""

import threading
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from app.config import get_settings
from app.tenants import Tenant


class RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self):
        self.requests = defaultdict(list)
        self.lock = threading.Lock()

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Record a request for `key` and report whether it is within the limit."""
        now = time.time()
        window_start = now - window_seconds

        with self.lock:
            self.requests[key] = [t for t in self.requests[key] if t > window_start]
            if len(self.requests[key]) >= max_requests:
                return False
            self.requests[key].append(now)
            return True

    def get_remaining(self, key: str, max_requests: int, window_seconds: int) -> int:
        """Requests left in the current window for `key`."""
        now = time.time()
        window_start = now - window_seconds

        with self.lock:
            recent = [t for t in self.requests[key] if t > window_start]
            return max(0, max_requests - len(recent))


# Global rate limiter instance
rate_limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    """Client IP, honouring the first hop of X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request, tenant: Tenant) -> None:
    """
    Enforce the tenant's rate limit for this client.

    Raises:
        HTTPException: 429 Too Many Requests if rate limited
    """
    settings = get_settings()
    key = f"{tenant.id}:{get_client_ip(request)}"
    limit = tenant.rate_limit_requests(settings)
    window = settings.rate_limit_window

    if not rate_limiter.is_allowed(key, limit, window):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please wait before sending more messages.",
            headers={"Retry-After": str(window)},
        )
