"""Tests for the iLink protocol client."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ilink_bot.client.client import (
    DEFAULT_BASE_URL,
    ILinkClient,
    _build_base_info,
    _random_wechat_uin,
)


class TestHelpers:
    def test_random_wechat_uin_is_base64(self):
        import base64

        uin = _random_wechat_uin()
        # Should be valid base64
        decoded = base64.b64decode(uin).decode()
        # Should decode to a number string
        assert decoded.isdigit()

    def test_random_wechat_uin_varies(self):
        uins = {_random_wechat_uin() for _ in range(10)}
        # Should produce different values (probabilistic but virtually certain)
        assert len(uins) > 1

    def test_build_base_info(self):
        info = _build_base_info()
        assert "channel_version" in info
        assert isinstance(info["channel_version"], str)


class TestILinkClientInit:
    def test_default_base_url(self):
        client = ILinkClient(token="test_token")
        assert client.base_url == DEFAULT_BASE_URL

    def test_custom_base_url(self):
        client = ILinkClient(token="test", base_url="https://custom.example.com/")
        assert client.base_url == "https://custom.example.com"  # trailing slash stripped

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("ILINK_TOKEN", "env_token_123")
        client = ILinkClient()
        assert client.token == "env_token_123"

    def test_is_authenticated(self):
        assert ILinkClient(token="tok").is_authenticated is True
        # Without token and without file
        client = ILinkClient(token_file="/nonexistent/path/token.json")
        assert client.is_authenticated is False

    def test_get_bot_info(self):
        client = ILinkClient(token="tok123")
        info = client.get_bot_info()
        assert info.connected is True
        assert info.base_url == DEFAULT_BASE_URL


class TestTokenPersistence:
    def test_save_and_load(self, tmp_path):
        token_file = tmp_path / "token.json"

        # Save
        client = ILinkClient(token="test_token_save", token_file=token_file)
        client._save_token("test_token_save", bot_id="bot1", user_id="user1")
        assert token_file.exists()

        # Check permissions (Unix only)
        import os

        if os.name != "nt":
            assert oct(token_file.stat().st_mode)[-3:] == "600"

        # Load
        client2 = ILinkClient(token_file=token_file)
        assert client2.token == "test_token_save"

    def test_load_missing_file(self, tmp_path):
        client = ILinkClient(token_file=tmp_path / "missing.json")
        assert client.token is None


@respx.mock
class TestAPIRequests:
    @pytest.mark.asyncio
    async def test_get_qrcode(self):
        respx.get(f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode").mock(
            return_value=httpx.Response(
                200,
                json={
                    "qrcode": "qr_abc123",
                    "qrcode_img_content": "https://example.com/qr.png",
                },
            )
        )

        async with ILinkClient(token="test") as client:
            qr = await client.get_qrcode()
            assert qr.qrcode == "qr_abc123"
            assert "example.com" in qr.qrcode_img_content

    @pytest.mark.asyncio
    async def test_get_updates_success(self):
        respx.post(f"{DEFAULT_BASE_URL}/ilink/bot/getupdates").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ret": 0,
                    "msgs": [
                        {
                            "message_id": 42,
                            "from_user_id": "user@im.wechat",
                            "message_type": 1,
                            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
                        }
                    ],
                    "get_updates_buf": "new_cursor",
                },
            )
        )

        async with ILinkClient(token="test") as client:
            resp = await client.get_updates("")
            assert resp.ret == 0
            assert len(resp.msgs) == 1
            assert resp.msgs[0].from_user_id == "user@im.wechat"
            assert resp.get_updates_buf == "new_cursor"

    @pytest.mark.asyncio
    async def test_get_updates_timeout(self):
        respx.post(f"{DEFAULT_BASE_URL}/ilink/bot/getupdates").mock(
            side_effect=httpx.ReadTimeout("timeout")
        )

        async with ILinkClient(token="test") as client:
            resp = await client.get_updates("cursor")
            # Timeout returns empty response
            assert resp.ret == 0
            assert len(resp.msgs) == 0
            assert resp.get_updates_buf == "cursor"

    @pytest.mark.asyncio
    async def test_send_text(self):
        route = respx.post(f"{DEFAULT_BASE_URL}/ilink/bot/sendmessage").mock(
            return_value=httpx.Response(200, json={})
        )

        async with ILinkClient(token="test") as client:
            result = await client.send_text("user@im.wechat", "hello!", context_token="ctx_1")
            assert "message_id" in result

            # Verify request body
            request = route.calls[0].request
            body = json.loads(request.content)
            assert body["msg"]["to_user_id"] == "user@im.wechat"
            assert body["msg"]["item_list"][0]["text_item"]["text"] == "hello!"
            assert body["msg"]["context_token"] == "ctx_1"

    @pytest.mark.asyncio
    async def test_send_typing(self):
        respx.post(f"{DEFAULT_BASE_URL}/ilink/bot/sendtyping").mock(
            return_value=httpx.Response(200, json={"ret": 0})
        )

        async with ILinkClient(token="test") as client:
            result = await client.send_typing("user@im.wechat", "ticket_abc")
            assert result["ret"] == 0

    @pytest.mark.asyncio
    async def test_get_config(self):
        respx.post(f"{DEFAULT_BASE_URL}/ilink/bot/getconfig").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ret": 0,
                    "typing_ticket": "ticket_xyz",
                },
            )
        )

        async with ILinkClient(token="test") as client:
            config = await client.get_config("user@im.wechat")
            assert config.typing_ticket == "ticket_xyz"

    @pytest.mark.asyncio
    async def test_headers_contain_required_fields(self):
        route = respx.post(f"{DEFAULT_BASE_URL}/ilink/bot/sendmessage").mock(
            return_value=httpx.Response(200, json={})
        )

        async with ILinkClient(token="my_token") as client:
            await client.send_text("user@im.wechat", "test")

            headers = dict(route.calls[0].request.headers)
            assert headers["authorizationtype"] == "ilink_bot_token"
            assert headers["authorization"] == "Bearer my_token"
            assert "x-wechat-uin" in headers
            assert headers["content-type"] == "application/json"

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with ILinkClient(token="test") as client:
            assert client.is_authenticated
        # After exit, client should be closed
        assert client._http is None or client._http.is_closed
