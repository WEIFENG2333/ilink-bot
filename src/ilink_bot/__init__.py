"""iLink Bot SDK — Standalone Python SDK for WeChat iLink Bot protocol."""

from ilink_bot.bot.bot import WeChatBot
from ilink_bot.bot.filters import filters
from ilink_bot.client.client import ILinkClient
from ilink_bot.client.rate_limiter import AsyncRateLimiter
from ilink_bot.models.messages import Message, MessageType

__version__ = "0.1.0"

__all__ = [
    "AsyncRateLimiter",
    "ILinkClient",
    "Message",
    "MessageType",
    "WeChatBot",
    "filters",
]
