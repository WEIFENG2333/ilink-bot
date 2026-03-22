"""Tests for the async token-bucket rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest

from ilink_bot.client.rate_limiter import AsyncRateLimiter


class TestAsyncRateLimiter:
    def test_init_validates_rate(self):
        with pytest.raises(ValueError, match="rate must be positive"):
            AsyncRateLimiter(rate=0)
        with pytest.raises(ValueError, match="rate must be positive"):
            AsyncRateLimiter(rate=-1.0)

    def test_init_validates_burst(self):
        with pytest.raises(ValueError, match="burst must be >= 1"):
            AsyncRateLimiter(rate=1.0, burst=0)

    @pytest.mark.asyncio
    async def test_burst_allows_immediate(self):
        limiter = AsyncRateLimiter(rate=1.0, burst=3)
        start = time.monotonic()
        for _ in range(3):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        # All 3 should be near-instant (< 0.1s)
        assert elapsed < 0.2

    @pytest.mark.asyncio
    async def test_rate_limits_after_burst(self):
        limiter = AsyncRateLimiter(rate=10.0, burst=1)
        # First acquire is instant (burst=1)
        await limiter.acquire()
        # Second acquire must wait ~0.1s (rate=10/s)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05  # at least ~0.1s but allow some slack

    def test_try_acquire_success(self):
        limiter = AsyncRateLimiter(rate=1.0, burst=1)
        assert limiter.try_acquire() is True

    def test_try_acquire_exhausted(self):
        limiter = AsyncRateLimiter(rate=1.0, burst=1)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False

    @pytest.mark.asyncio
    async def test_tokens_replenish(self):
        limiter = AsyncRateLimiter(rate=100.0, burst=1)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False
        await asyncio.sleep(0.02)  # 100 tokens/s → 2 tokens in 0.02s
        assert limiter.try_acquire() is True
