"""Layer 2 — Event-driven WeChat Bot framework.

Manages the long-poll lifecycle, cursor persistence, reconnection with
exponential backoff, and routes inbound messages through registered handlers.

Usage::

    from ilink_bot import WeChatBot, filters

    bot = WeChatBot()

    @bot.on_message(filters.text)
    async def echo(msg):
        await msg.reply(f"You said: {msg.text}")

    bot.run()
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ilink_bot.client.client import DEFAULT_TOKEN_FILE, ILinkClient
from ilink_bot.models.messages import Message, WeChatMessage

if TYPE_CHECKING:
    from ilink_bot.bot.filters import Filter

logger = logging.getLogger("ilink_bot.bot")

# Type aliases
MessageHandler = Callable[[Message], Awaitable[Any]]
ErrorHandler = Callable[[Exception, Message | None], Awaitable[Any]]

# Reconnection parameters
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
BACKOFF_FACTOR = 2.0
MAX_CONSECUTIVE_FAILURES = 5

# Session expired error code from upstream
SESSION_EXPIRED_ERRCODE = -14


class _HandlerEntry:
    """Internal: a handler + its associated filter."""

    __slots__ = ("filter", "handler", "priority")

    def __init__(self, filt: Filter | None, handler: MessageHandler, priority: int = 0) -> None:
        self.filter = filt
        self.handler = handler
        self.priority = priority


class WeChatBot:
    """High-level event-driven bot built on top of :class:`ILinkClient`.

    Parameters
    ----------
    token:
        Bot token.  Falls back to ``ILINK_TOKEN`` env var then *token_file*.
    base_url:
        iLink API base URL.
    token_file:
        Path to persist / load the bot token.
    cursor_file:
        Path to persist the long-poll cursor so messages are not replayed on restart.
    max_concurrent:
        Maximum number of message handlers running concurrently.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str = "https://ilinkai.weixin.qq.com",
        token_file: str | Path | None = None,
        cursor_file: str | Path | None = None,
        max_concurrent: int = 10,
    ) -> None:
        tf = Path(token_file) if token_file else DEFAULT_TOKEN_FILE
        self._client = ILinkClient(token=token, base_url=base_url, token_file=tf)
        self._cursor_file = Path(cursor_file) if cursor_file else tf.parent / "cursor.json"
        self._handlers: list[_HandlerEntry] = []
        self._error_handler: ErrorHandler | None = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = False
        self._cursor: str = ""

    # -- public properties -------------------------------------------------

    @property
    def client(self) -> ILinkClient:
        """Access the underlying :class:`ILinkClient`."""
        return self._client

    @property
    def is_running(self) -> bool:
        return self._running

    # -- decorator registration --------------------------------------------

    def on_message(
        self,
        filter: Filter | None = None,
        *,
        priority: int = 0,
    ) -> Callable[[MessageHandler], MessageHandler]:
        """Register a message handler with an optional filter.

        Example::

            @bot.on_message(filters.text)
            async def handle(msg: Message):
                await msg.reply("Got it!")
        """

        def decorator(func: MessageHandler) -> MessageHandler:
            self._handlers.append(_HandlerEntry(filter, func, priority))
            # Sort by priority (higher first) so important handlers run first
            self._handlers.sort(key=lambda h: h.priority, reverse=True)
            return func

        return decorator

    def on_error(self, func: ErrorHandler) -> ErrorHandler:
        """Register a global error handler for unhandled exceptions in message handlers."""
        self._error_handler = func
        return func

    # -- cursor persistence ------------------------------------------------

    def _load_cursor(self) -> str:
        if not self._cursor_file.exists():
            return ""
        try:
            data = json.loads(self._cursor_file.read_text())
            return str(data.get("get_updates_buf", ""))
        except Exception:
            logger.warning("Failed to load cursor from %s", self._cursor_file, exc_info=True)
            return ""

    def _save_cursor(self, cursor: str) -> None:
        try:
            self._cursor_file.parent.mkdir(parents=True, exist_ok=True)
            self._cursor_file.write_text(json.dumps({"get_updates_buf": cursor}))
        except Exception:
            logger.warning("Failed to save cursor to %s", self._cursor_file, exc_info=True)

    # -- message dispatch --------------------------------------------------

    async def _dispatch(self, raw: WeChatMessage) -> None:
        """Route a single inbound message through registered handlers."""
        # Only process USER messages (ignore bot's own messages)
        if raw.message_type != 1:  # MessageType.USER
            return
        if not raw.item_list:
            return

        msg = Message(raw, client=self._client)

        for entry in self._handlers:
            if entry.filter is None or entry.filter(msg):
                async with self._semaphore:
                    try:
                        await entry.handler(msg)
                    except Exception as exc:
                        logger.error(
                            "Handler %s raised %s: %s",
                            entry.handler.__name__,
                            type(exc).__name__,
                            exc,
                            exc_info=True,
                        )
                        if self._error_handler:
                            try:
                                await self._error_handler(exc, msg)
                            except Exception:
                                logger.error("Error handler itself failed", exc_info=True)
                # First matching handler wins (like python-telegram-bot)
                return

        logger.debug("No handler matched message id=%s type=%s", msg.id, msg.type)

    # -- main loop ---------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Core long-poll loop with automatic reconnection."""
        self._cursor = self._load_cursor()
        backoff = INITIAL_BACKOFF
        consecutive_failures = 0

        logger.info(
            "Starting poll loop (base_url=%s, cursor_len=%d)",
            self._client.base_url,
            len(self._cursor),
        )

        while self._running:
            try:
                resp = await self._client.get_updates(self._cursor)

                # Check for API errors
                is_error = (resp.ret is not None and resp.ret != 0) or (
                    resp.errcode is not None and resp.errcode != 0
                )

                if is_error:
                    if (
                        resp.errcode == SESSION_EXPIRED_ERRCODE
                        or resp.ret == SESSION_EXPIRED_ERRCODE
                    ):
                        logger.error("Session expired (errcode=%s), pausing 5 min", resp.errcode)
                        await asyncio.sleep(300)
                        continue

                    consecutive_failures += 1
                    logger.warning(
                        "getUpdates error: ret=%s errcode=%s errmsg=%s (%d/%d)",
                        resp.ret,
                        resp.errcode,
                        resp.errmsg,
                        consecutive_failures,
                        MAX_CONSECUTIVE_FAILURES,
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        logger.error("Too many failures, backing off %.1fs", MAX_BACKOFF)
                        consecutive_failures = 0
                        await asyncio.sleep(MAX_BACKOFF)
                    else:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * BACKOFF_FACTOR, MAX_BACKOFF)
                    continue

                # Success — reset backoff
                consecutive_failures = 0
                backoff = INITIAL_BACKOFF

                # Persist cursor
                if resp.get_updates_buf:
                    self._cursor = resp.get_updates_buf
                    self._save_cursor(self._cursor)

                # Dispatch messages concurrently
                tasks = [self._dispatch(msg) for msg in resp.msgs]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            except asyncio.CancelledError:
                break
            except Exception:
                consecutive_failures += 1
                logger.error(
                    "Poll loop exception (%d/%d)",
                    consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * BACKOFF_FACTOR, MAX_BACKOFF)

        logger.info("Poll loop stopped")

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the bot (non-blocking, returns immediately)."""
        if not self._client.is_authenticated:
            raise RuntimeError(
                "Bot is not authenticated. Call `await bot.client.login()` first or "
                "provide a token via ILINK_TOKEN env var / token_file."
            )
        self._running = True
        logger.info("Bot starting...")
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        self._running = False
        await self._client.close()
        logger.info("Bot stopped")

    def run(self) -> None:
        """Blocking entry point — sets up signal handlers and runs the event loop.

        Equivalent to::

            asyncio.run(bot.start())
            # ... wait for Ctrl+C ...
            asyncio.run(bot.stop())
        """

        async def _main() -> None:
            loop = asyncio.get_running_loop()
            stop_event = asyncio.Event()

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)

            await self.start()
            logger.info("Bot is running. Press Ctrl+C to stop.")
            await stop_event.wait()
            logger.info("Shutting down...")
            await self.stop()

        asyncio.run(_main())
