"""Async token-bucket rate limiter.

Provides a simple, coroutine-safe rate limiter based on the *token bucket*
algorithm.  Tokens are replenished at a fixed rate and may accumulate up to
a configurable *burst* capacity.

Usage example::

    limiter = AsyncRateLimiter(rate=5.0, burst=10)

    async def send(msg):
        await limiter.acquire()  # blocks until a token is available
        ...
"""

from __future__ import annotations

import asyncio
import time


class AsyncRateLimiter:
    """Token-bucket rate limiter for ``asyncio`` applications.

    Parameters
    ----------
    rate:
        Number of tokens added per second (i.e. the sustained throughput).
        Defaults to ``1.0`` (one operation per second).
    burst:
        Maximum number of tokens that can accumulate.  This is also the
        initial token count.  Defaults to ``1``.
    """

    def __init__(self, rate: float = 1.0, burst: int = 1) -> None:
        if rate <= 0:
            msg = f"rate must be positive, got {rate}"
            raise ValueError(msg)
        if burst < 1:
            msg = f"burst must be >= 1, got {burst}"
            raise ValueError(msg)

        self._rate: float = rate
        self._burst: int = burst
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Add tokens accrued since the last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it.

        If the bucket is empty the coroutine sleeps until at least one
        token has been replenished, then consumes exactly one token.
        """
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Calculate how long until the next token arrives
                wait = (1.0 - self._tokens) / self._rate

            await asyncio.sleep(wait)

    def try_acquire(self) -> bool:
        """Try to consume a token without waiting.

        Returns
        -------
        bool
            ``True`` if a token was available and consumed, ``False``
            otherwise.

        Note
        ----
        This method is **not** coroutine-safe on its own because it does
        not hold the internal lock asynchronously.  It is intended for
        best-effort, non-blocking checks (e.g. in synchronous callbacks).
        For guaranteed correctness across concurrent coroutines, prefer
        :meth:`acquire`.
        """
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
