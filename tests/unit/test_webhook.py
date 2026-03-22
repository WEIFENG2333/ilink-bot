"""Tests for the webhook gateway."""

from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest
import respx

from ilink_bot.models.messages import WeChatMessage
from ilink_bot.webhook.gateway import WebhookConfig, WebhookGateway


class TestWebhookConfig:
    def test_defaults(self):
        config = WebhookConfig(url="https://example.com/hook")
        assert config.secret == ""
        assert config.timeout == 10.0
        assert config.max_retries == 3

    def test_custom(self):
        config = WebhookConfig(url="https://x.com", secret="s3cret", max_retries=5)
        assert config.secret == "s3cret"
        assert config.max_retries == 5


class TestWebhookGateway:
    def _make_gateway(self, *, url: str = "https://hook.example.com", secret: str = "test_secret"):
        from ilink_bot.client.client import ILinkClient
        client = ILinkClient(token="dummy")
        config = WebhookConfig(url=url, secret=secret)
        return WebhookGateway(client=client, config=config)

    def test_sign(self):
        gw = self._make_gateway(secret="my_secret")
        payload = b'{"id": "1"}'
        sig = gw._sign(payload)
        expected = hmac.new(b"my_secret", payload, hashlib.sha256).hexdigest()
        assert sig == expected

    def test_sign_empty_secret(self):
        gw = self._make_gateway(secret="")
        sig = gw._sign(b"payload")
        assert sig == ""

    def test_format_text_message(self):
        gw = self._make_gateway()
        msg = WeChatMessage(
            message_id=42,
            from_user_id="user@im.wechat",
            create_time_ms=1742000000000,
            context_token="ctx_1",
            item_list=[{"type": 1, "text_item": {"text": "hello"}}],
        )
        payload = gw._format_message(msg)
        assert payload["id"] == "42"
        assert payload["from_user"] == "user@im.wechat"
        assert payload["type"] == "text"
        assert payload["content"] == "hello"
        assert payload["timestamp"] == 1742000000000

    def test_format_image_message(self):
        gw = self._make_gateway()
        msg = WeChatMessage(
            message_id=43,
            from_user_id="user@im.wechat",
            item_list=[{"type": 2, "image_item": {"media": {}}}],
        )
        payload = gw._format_message(msg)
        assert payload["type"] == "image"
        assert payload["content"] == ""

    def test_format_voice_message(self):
        gw = self._make_gateway()
        msg = WeChatMessage(
            message_id=44,
            from_user_id="user@im.wechat",
            item_list=[{"type": 3, "voice_item": {"text": "voice text"}}],
        )
        payload = gw._format_message(msg)
        assert payload["type"] == "voice"
        assert payload["content"] == "voice text"

    @respx.mock
    @pytest.mark.asyncio
    async def test_push_success(self):
        respx.post("https://hook.example.com").mock(
            return_value=httpx.Response(200)
        )

        gw = self._make_gateway()
        result = await gw._push({"id": "1", "content": "test"})
        assert result is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_push_4xx_no_retry(self):
        respx.post("https://hook.example.com").mock(
            return_value=httpx.Response(400)
        )

        gw = self._make_gateway()
        gw.config.max_retries = 3
        result = await gw._push({"id": "1"})
        assert result is False
        # Should NOT retry on 4xx
        assert len(respx.calls) == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_push_signature_header(self):
        route = respx.post("https://hook.example.com").mock(
            return_value=httpx.Response(200)
        )

        gw = self._make_gateway(secret="my_secret")
        await gw._push({"id": "1"})

        request = route.calls[0].request
        assert "x-ilink-signature" in dict(request.headers)
        assert "x-ilink-timestamp" in dict(request.headers)
