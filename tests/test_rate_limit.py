"""Tests for the in-memory rate limiter and its HTTP behaviour."""

import time

import pytest

from app.config import get_settings
from app.middleware import rate_limit as rl
from tests.conftest import chat_payload


class TestRateLimiter:
    def test_allows_up_to_limit_then_blocks(self):
        limiter = rl.RateLimiter()
        assert all(limiter.is_allowed("ip", 3, 60) for _ in range(3))
        assert limiter.is_allowed("ip", 3, 60) is False

    def test_keys_are_independent(self):
        limiter = rl.RateLimiter()
        for _ in range(3):
            limiter.is_allowed("a", 3, 60)
        assert limiter.is_allowed("b", 3, 60) is True

    def test_window_expires(self, monkeypatch):
        limiter = rl.RateLimiter()
        now = [1000.0]
        monkeypatch.setattr(time, "time", lambda: now[0])
        for _ in range(2):
            limiter.is_allowed("ip", 2, 10)
        assert limiter.is_allowed("ip", 2, 10) is False
        now[0] += 11
        assert limiter.is_allowed("ip", 2, 10) is True

    def test_remaining_count(self):
        limiter = rl.RateLimiter()
        limiter.is_allowed("ip", 5, 60)
        assert limiter.get_remaining("ip", 5, 60) == 4


class TestHttp:
    @pytest.fixture(autouse=True)
    def small_limit(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "rate_limit_requests", 2)
        monkeypatch.setattr(rl, "rate_limiter", rl.RateLimiter())

    def test_429_after_limit_with_retry_after(self, client, fake_llm):
        fake_llm("ok")
        headers = {"X-Forwarded-For": "203.0.113.7"}
        assert client.post("/chat", json=chat_payload("a"), headers=headers).status_code == 200
        assert client.post("/chat", json=chat_payload("a"), headers=headers).status_code == 200
        r = client.post("/chat", json=chat_payload("a"), headers=headers)
        assert r.status_code == 429
        assert r.headers["retry-after"] == str(get_settings().rate_limit_window)

    def test_forwarded_header_identifies_client(self, client, fake_llm):
        fake_llm("ok")
        for ip in ("198.51.100.1", "198.51.100.1", "198.51.100.2"):
            r = client.post("/chat", json=chat_payload("a"), headers={"X-Forwarded-For": ip})
            assert r.status_code == 200, ip
