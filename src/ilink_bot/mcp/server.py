"""MCP Server exposing WeChat messaging as tools.

Exposes a minimal set of tools following the MCP "Less is More" principle:

- ``wechat_send_message``  — Send a message to a WeChat user
- ``wechat_get_messages``  — Retrieve recent messages
- ``wechat_bot_status``    — Check bot connection status

Usage::

    # stdio mode (Claude Desktop / Claude Code)
    ilink-bot mcp

    # HTTP mode (remote deployment)
    ilink-bot mcp --transport http --port 8080

Requires the ``mcp`` extra: ``pip install ilink-bot[mcp]``
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger("ilink_bot.mcp")

try:
    from mcp.server.fastmcp import Context, FastMCP  # type: ignore[import-untyped,unused-ignore]
except ImportError:
    FastMCP = None  # type: ignore[assignment,misc,unused-ignore]
    Context = None  # type: ignore[assignment,misc,unused-ignore]


def _check_mcp_available() -> None:
    """Raise a helpful error if the ``mcp`` package is not installed."""
    if FastMCP is None:
        raise ImportError(
            "MCP support requires the 'mcp' extra. Install with: pip install ilink-bot[mcp]"
        )


# ---------------------------------------------------------------------------
# Helper: extract text / type from a raw message object
# ---------------------------------------------------------------------------


def _extract_text(msg: Any) -> str:
    """Extract the text content from a message's item_list."""
    for item in msg.item_list or []:
        if item.type == 1 and item.text_item:
            return str(item.text_item.text or "")
        if item.type == 3 and item.voice_item and item.voice_item.text:
            return str(item.voice_item.text)
    return ""


def _extract_type(msg: Any) -> str:
    """Return a human-readable type string for the first item in a message."""
    type_map = {0: "none", 1: "text", 2: "image", 3: "voice", 4: "file", 5: "video"}
    for item in msg.item_list or []:
        return type_map.get(item.type or 0, "unknown")
    return "none"


# ---------------------------------------------------------------------------
# Lifespan: manages ILinkClient + background polling task
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:  # type: ignore[type-arg,unused-ignore]
    """Manage the ILinkClient lifecycle and background message polling.

    Yields a dict containing:
    - ``client``: the :class:`~ilink_bot.client.client.ILinkClient` instance
    - ``messages``: a shared list of recently received messages
    - ``poll_event``: an :class:`asyncio.Event` set once polling has started
    """
    from ilink_bot.client.client import ILinkClient

    client = ILinkClient()

    if not client.is_authenticated:
        logger.warning("Bot not authenticated. Run `ilink-bot login` first or set ILINK_TOKEN.")

    messages: list[dict[str, Any]] = []
    poll_event = asyncio.Event()

    async def _poll_loop() -> None:
        """Background loop: long-poll for new messages."""
        cursor = ""
        poll_event.set()
        while True:
            try:
                resp = await client.get_updates(cursor)
                if resp.get_updates_buf:
                    cursor = resp.get_updates_buf
                for msg in resp.msgs:
                    messages.append(
                        {
                            "from": msg.from_user_id or "",
                            "text": _extract_text(msg),
                            "type": _extract_type(msg),
                            "timestamp": msg.create_time_ms,
                        }
                    )
                    # Keep only the last 100 messages
                    if len(messages) > 100:
                        messages.pop(0)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("MCP poll error", exc_info=True)
                await asyncio.sleep(5)

    poll_task: asyncio.Task[None] | None = None
    if client.is_authenticated:
        poll_task = asyncio.create_task(_poll_loop())

    try:
        yield {
            "client": client,
            "messages": messages,
            "poll_event": poll_event,
        }
    finally:
        if poll_task is not None and not poll_task.done():
            poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await poll_task
        await client.close()
        logger.info("MCP lifespan shutdown complete")


# ---------------------------------------------------------------------------
# FastMCP server instance
# ---------------------------------------------------------------------------


def _create_mcp() -> FastMCP:  # type: ignore[type-arg,unused-ignore]
    """Create the FastMCP server (deferred so import-time errors are avoidable)."""
    _check_mcp_available()
    return FastMCP("WeChat iLink Bot", lifespan=lifespan)


mcp: FastMCP = _create_mcp() if FastMCP is not None else None  # type: ignore[assignment,unused-ignore]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

if mcp is not None:

    @mcp.tool()
    async def wechat_send_message(
        to_user_id: str,
        content: str,
        ctx: Context,  # type: ignore[type-arg,unused-ignore]
        context_token: str | None = None,
    ) -> str:
        """Send a text message to a WeChat user.

        Args:
            to_user_id: The recipient's WeChat user ID (xxx@im.wechat format).
            content: The text message body to send.
            ctx: MCP request context (injected automatically).
            context_token: Optional context token for conversation threading.

        Returns:
            A confirmation string containing the client-side message ID.
        """
        lifespan_ctx: dict[str, Any] = ctx.request_context.lifespan_context
        client = lifespan_ctx["client"]

        if not client.is_authenticated:
            return "Error: bot is not authenticated. Run `ilink-bot login` first."

        result = await client.send_text(to_user_id, content, context_token=context_token)
        return f"Message sent (id={result.get('message_id', 'unknown')})"

    @mcp.tool()
    async def wechat_get_messages(
        ctx: Context,  # type: ignore[type-arg,unused-ignore]
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get recent messages received by the bot.

        Returns the most recent messages from the in-memory cache populated
        by the background polling task.

        Args:
            ctx: MCP request context (injected automatically).
            limit: Maximum number of messages to return (1-100, default 10).

        Returns:
            A list of message dicts with keys: from, text, type, timestamp.
        """
        lifespan_ctx: dict[str, Any] = ctx.request_context.lifespan_context
        messages: list[dict[str, Any]] = lifespan_ctx["messages"]

        limit = min(max(1, limit), 100)
        return messages[-limit:]

    @mcp.tool()
    async def wechat_bot_status(ctx: Context) -> dict[str, Any]:  # type: ignore[type-arg,unused-ignore]
        """Check the current bot connection and authentication status.

        Args:
            ctx: MCP request context (injected automatically).

        Returns:
            A dict with keys: connected, bot_id, base_url, cached_messages.
            On error, returns: connected=False and an error message.
        """
        lifespan_ctx: dict[str, Any] = ctx.request_context.lifespan_context
        client = lifespan_ctx["client"]
        messages: list[dict[str, Any]] = lifespan_ctx["messages"]

        try:
            info = client.get_bot_info()
            return {
                "connected": info.connected,
                "bot_id": info.bot_id,
                "base_url": info.base_url,
                "cached_messages": len(messages),
            }
        except Exception as exc:
            return {"connected": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run_mcp_server(
    transport: str = "stdio",
    port: int = 8080,
    token: str | None = None,
    token_file: str | None = None,
) -> None:
    """Start the MCP server with the specified transport.

    Parameters
    ----------
    transport:
        ``"stdio"`` for local Claude Desktop / Claude Code integration, or
        ``"http"`` for remote deployment via streamable-http.
    port:
        Port to bind when using HTTP transport (default 8080).
    token:
        Bot token. If ``None``, falls back to ``ILINK_TOKEN`` env var or
        the persisted token file.
    token_file:
        Path to a token file. Overrides the default
        ``~/.ilink-bot/token.json``.

    Raises
    ------
    ImportError
        If the ``mcp`` package is not installed.
    """
    _check_mcp_available()

    # Ensure the global mcp instance is available
    global mcp
    if mcp is None:
        mcp = _create_mcp()

    # If explicit token / token_file are provided, set env vars so that
    # ILinkClient picks them up during lifespan initialisation.
    import os

    if token:
        os.environ["ILINK_TOKEN"] = token
    if token_file:
        os.environ["ILINK_TOKEN_FILE"] = token_file

    logger.info("Starting MCP server (transport=%s)", transport)

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", port=port)  # type: ignore[call-arg,unused-ignore]
