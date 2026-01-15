"""
Rate limiting and cost protection middleware.
"""

from fastapi import Request, HTTPException
from functools import lru_cache
import time
from collections import defaultdict
import threading

from app.config import get_settings

settings = get_settings()


class RateLimiter:
    """Simple in-memory rate limiter."""

    def __init__(self):
        self.requests = defaultdict(list)
        self.lock = threading.Lock()

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """
        Check if a request is allowed under the rate limit.

        Args:
            key: Unique identifier (e.g., IP address)
            max_requests: Maximum requests allowed in the window
            window_seconds: Time window in seconds

        Returns:
            True if allowed, False if rate limited
        """
        now = time.time()
        window_start = now - window_seconds

        with self.lock:
            # Clean old requests
            self.requests[key] = [t for t in self.requests[key] if t > window_start]

            # Check limit
            if len(self.requests[key]) >= max_requests:
                return False

            # Record this request
            self.requests[key].append(now)
            return True

    def get_remaining(self, key: str, max_requests: int, window_seconds: int) -> int:
        """Get remaining requests in the current window."""
        now = time.time()
        window_start = now - window_seconds

        with self.lock:
            recent = [t for t in self.requests[key] if t > window_start]
            return max(0, max_requests - len(recent))


# Global rate limiter instance
rate_limiter = RateLimiter()


def get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    # Check for forwarded headers (for proxies/load balancers)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request) -> None:
    """
    Check rate limit and raise HTTPException if exceeded.

    Raises:
        HTTPException: 429 Too Many Requests if rate limited
    """
    client_ip = get_client_ip(request)

    if not rate_limiter.is_allowed(
        key=client_ip,
        max_requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window,
    ):
        remaining = rate_limiter.get_remaining(
            client_ip, settings.rate_limit_requests, settings.rate_limit_window
        )
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Please wait before sending more messages.",
            headers={"Retry-After": str(settings.rate_limit_window)},
        )
