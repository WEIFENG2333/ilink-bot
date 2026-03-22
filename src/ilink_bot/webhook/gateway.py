"""Webhook Gateway — converts iLink long-poll to outbound HTTP POST webhooks.

Runs a long-poll loop internally and pushes each inbound message as an HTTP
POST to the configured webhook URL with HMAC-SHA256 signature verification.

Usage::

    ilink-bot webhook --url https://your-server.com/wechat --secret mysecret
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from ilink_bot.client.client import ILinkClient
    from ilink_bot.models.messages import WeChatMessage

logger = logging.getLogger("ilink_bot.webhook")


@dataclass
class WebhookConfig:
    """Configuration for the webhook gateway."""

    url: str
    secret: str = ""
    timeout: float = 10.0
    max_retries: int = 3
    retry_backoff: float = 1.0


@dataclass
class WebhookGateway:
    """Converts iLink long-poll messages into outbound HTTP webhooks."""

    client: ILinkClient
    config: WebhookConfig
    _cursor: str = ""
    _running: bool = False
    _http: httpx.AsyncClient | None = field(default=None, repr=False)

    def _sign(self, payload: bytes) -> str:
        """Compute HMAC-SHA256 signature for the payload."""
        if not self.config.secret:
            return ""
        return hmac.new(
            self.config.secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()

    def _format_message(self, msg: WeChatMessage) -> dict[str, object]:
        """Convert a raw message to the webhook payload format."""
        text = ""
        msg_type = "unknown"
        for item in msg.item_list or []:
            if item.type == 1 and item.text_item:
                text = item.text_item.text or ""
                msg_type = "text"
                break
            if item.type == 2:
                msg_type = "image"
                break
            if item.type == 3:
                msg_type = "voice"
                if item.voice_item and item.voice_item.text:
                    text = item.voice_item.text
                break
            if item.type == 4:
                msg_type = "file"
                break
            if item.type == 5:
                msg_type = "video"
                break

        return {
            "id": str(msg.message_id or ""),
            "from_user": msg.from_user_id or "",
            "type": msg_type,
            "content": text,
            "timestamp": msg.create_time_ms or 0,
            "context_token": msg.context_token or "",
        }

    async def _push(self, payload: dict[str, object]) -> bool:
        """Push a single webhook payload with retries."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.config.timeout)

        body = json.dumps(payload, ensure_ascii=False).encode()
        signature = self._sign(body)
        headers = {
            "Content-Type": "application/json",
            "X-ILink-Signature": signature,
            "X-ILink-Timestamp": str(int(time.time())),
        }

        for attempt in range(self.config.max_retries):
            try:
                resp = await self._http.post(self.config.url, content=body, headers=headers)
                if resp.status_code < 400:
                    return True
                if resp.status_code < 500:
                    logger.warning(
                        "Webhook 4xx (no retry): %d %s", resp.status_code, resp.text[:200]
                    )
                    return False
                logger.warning(
                    "Webhook 5xx: %d (attempt %d/%d)",
                    resp.status_code,
                    attempt + 1,
                    self.config.max_retries,
                )
            except Exception:
                logger.error(
                    "Webhook push error (attempt %d/%d)",
                    attempt + 1,
                    self.config.max_retries,
                    exc_info=True,
                )

            if attempt < self.config.max_retries - 1:
                delay = self.config.retry_backoff * (2**attempt)
                await asyncio.sleep(delay)

        logger.error("Webhook push failed after %d retries", self.config.max_retries)
        return False

    async def run(self) -> None:
        """Start the gateway loop (blocks until stopped)."""
        self._running = True
        logger.info("Webhook gateway starting (url=%s)", self.config.url)

        while self._running:
            try:
                resp = await self.client.get_updates(self._cursor)

                is_error = (resp.ret is not None and resp.ret != 0) or (
                    resp.errcode is not None and resp.errcode != 0
                )
                if is_error:
                    logger.warning("getUpdates error: ret=%s errcode=%s", resp.ret, resp.errcode)
                    await asyncio.sleep(5)
                    continue

                if resp.get_updates_buf:
                    self._cursor = resp.get_updates_buf

                for msg in resp.msgs:
                    if msg.message_type != 1:  # Only user messages
                        continue
                    payload = self._format_message(msg)
                    await self._push(payload)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Gateway loop error", exc_info=True)
                await asyncio.sleep(5)

        if self._http and not self._http.is_closed:
            await self._http.aclose()
        logger.info("Webhook gateway stopped")

    async def stop(self) -> None:
        self._running = False
