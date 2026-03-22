"""MCP Server exposing WeChat messaging as tools.

Exposes a minimal set of tools following the MCP "Less is More" principle:

- ``wechat_send_message``  — Send a message to the current user
- ``wechat_get_messages``  — Retrieve recent messages
- ``wechat_bot_status``    — Check bot connection status

Usage::

    # stdio mode (Claude Desktop / Claude Code)
    ilink-bot mcp

    # HTTP mode (remote deployment)
    ilink-bot mcp --transport http --port 8080
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("ilink_bot.mcp")


def create_mcp_server(
    token: str | None = None,
    token_file: str | None = None,
) -> Any:
    """Create and configure the MCP server instance.

    Requires the ``mcp`` extra: ``pip install ilink-bot[mcp]``
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise ImportError(
            "MCP support requires the 'mcp' extra. Install with: pip install ilink-bot[mcp]"
        ) from exc

    from ilink_bot.client.client import ILinkClient

    mcp = MCPServer("WeChat iLink Bot")

    # Shared state
    _client: ILinkClient | None = None
    _messages_cache: list[dict[str, Any]] = []
    _poll_task: asyncio.Task[None] | None = None
    _cursor: str = ""

    async def _get_client() -> ILinkClient:
        nonlocal _client
        if _client is None:
            _client = ILinkClient(token=token, token_file=token_file)
            if not _client.is_authenticated:
                raise RuntimeError(
                    "Bot not authenticated. Run `ilink-bot login` first or set ILINK_TOKEN."
                )
        return _client

    async def _ensure_polling() -> None:
        """Start background polling if not already running."""
        nonlocal _poll_task

        if _poll_task is not None and not _poll_task.done():
            return

        async def _poll_loop() -> None:
            nonlocal _cursor
            client = await _get_client()
            while True:
                try:
                    resp = await client.get_updates(_cursor)
                    if resp.get_updates_buf:
                        _cursor = resp.get_updates_buf
                    for msg in resp.msgs:
                        _messages_cache.append({
                            "from": msg.from_user_id or "",
                            "text": _extract_text(msg),
                            "type": _extract_type(msg),
                            "timestamp": msg.create_time_ms,
                        })
                        # Keep only last 100 messages
                        if len(_messages_cache) > 100:
                            _messages_cache.pop(0)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.error("MCP poll error", exc_info=True)
                    await asyncio.sleep(5)

        _poll_task = asyncio.create_task(_poll_loop())

    def _extract_text(msg: Any) -> str:
        for item in msg.item_list or []:
            if item.type == 1 and item.text_item:
                return item.text_item.text or ""
            if item.type == 3 and item.voice_item and item.voice_item.text:
                return item.voice_item.text
        return ""

    def _extract_type(msg: Any) -> str:
        type_map = {0: "none", 1: "text", 2: "image", 3: "voice", 4: "file", 5: "video"}
        for item in msg.item_list or []:
            return type_map.get(item.type or 0, "unknown")
        return "none"

    # ----- MCP Tools -----

    @mcp.tool()
    async def wechat_send_message(
        to_user_id: str,
        content: str,
        context_token: str | None = None,
    ) -> str:
        """Send a text message to a WeChat user.

        Args:
            to_user_id: The recipient's WeChat user ID (xxx@im.wechat format)
            content: The text message to send
            context_token: Optional context token for conversation association
        """
        client = await _get_client()
        result = await client.send_text(to_user_id, content, context_token=context_token)
        return f"Message sent (id={result.get('message_id', 'unknown')})"

    @mcp.tool()
    async def wechat_get_messages(limit: int = 10) -> list[dict[str, Any]]:
        """Get recent messages received by the bot.

        Args:
            limit: Maximum number of messages to return (default: 10, max: 100)
        """
        await _ensure_polling()
        limit = min(max(1, limit), 100)
        return _messages_cache[-limit:]

    @mcp.tool()
    async def wechat_bot_status() -> dict[str, Any]:
        """Check the current bot connection status."""
        try:
            client = await _get_client()
            info = client.get_bot_info()
            return {
                "connected": info.connected,
                "bot_id": info.bot_id,
                "base_url": info.base_url,
                "cached_messages": len(_messages_cache),
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}

    return mcp


def run_mcp_server(
    transport: str = "stdio",
    port: int = 8080,
    token: str | None = None,
    token_file: str | None = None,
) -> None:
    """Start the MCP server with the specified transport."""
    mcp = create_mcp_server(token=token, token_file=token_file)
    logger.info("Starting MCP server (transport=%s)", transport)
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", port=port)
