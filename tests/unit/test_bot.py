"""Tests for the bot framework."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ilink_bot.bot.bot import WeChatBot
from ilink_bot.bot.filters import filters
from ilink_bot.models.messages import Message, WeChatMessage


class TestHandlerRegistration:
    def test_register_handler(self):
        bot = WeChatBot(token="test")
        handler = AsyncMock()
        bot.on_message(filters.text)(handler)
        assert len(bot._handlers) == 1
        assert bot._handlers[0].handler is handler

    def test_register_multiple_handlers(self):
        bot = WeChatBot(token="test")
        h1 = AsyncMock()
        h2 = AsyncMock()
        bot.on_message(filters.text)(h1)
        bot.on_message(filters.image)(h2)
        assert len(bot._handlers) == 2

    def test_priority_ordering(self):
        bot = WeChatBot(token="test")
        low = AsyncMock(__name__="low")
        high = AsyncMock(__name__="high")
        bot.on_message(filters.text, priority=0)(low)
        bot.on_message(filters.text, priority=10)(high)
        # Higher priority should come first
        assert bot._handlers[0].handler is high

    def test_register_error_handler(self):
        bot = WeChatBot(token="test")
        handler = AsyncMock()
        bot.on_error(handler)
        assert bot._error_handler is handler


class TestMessageDispatch:
    def _make_raw(self, text: str = "hello") -> WeChatMessage:
        return WeChatMessage(
            message_id=1,
            from_user_id="user@im.wechat",
            message_type=1,  # USER
            item_list=[{"type": 1, "text_item": {"text": text}}],
        )

    @pytest.mark.asyncio
    async def test_dispatch_matches_filter(self):
        bot = WeChatBot(token="test")
        handler = AsyncMock()
        bot.on_message(filters.text)(handler)

        await bot._dispatch(self._make_raw("hello"))
        handler.assert_called_once()
        msg = handler.call_args[0][0]
        assert isinstance(msg, Message)
        assert msg.text == "hello"

    @pytest.mark.asyncio
    async def test_dispatch_no_match(self):
        bot = WeChatBot(token="test")
        handler = AsyncMock()
        bot.on_message(filters.image)(handler)  # Only matches images

        await bot._dispatch(self._make_raw("hello"))  # Text message
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_first_match_wins(self):
        bot = WeChatBot(token="test")
        h1 = AsyncMock()
        h2 = AsyncMock()
        bot.on_message(filters.text)(h1)
        bot.on_message(filters.all)(h2)

        await bot._dispatch(self._make_raw("test"))
        h1.assert_called_once()
        h2.assert_not_called()  # First match wins

    @pytest.mark.asyncio
    async def test_dispatch_skips_bot_messages(self):
        bot = WeChatBot(token="test")
        handler = AsyncMock()
        bot.on_message(filters.all)(handler)

        raw = WeChatMessage(
            message_id=1,
            message_type=2,  # BOT message
            item_list=[{"type": 1, "text_item": {"text": "bot reply"}}],
        )
        await bot._dispatch(raw)
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_error_handler(self):
        bot = WeChatBot(token="test")

        async def failing_handler(msg):
            raise ValueError("test error")

        error_handler = AsyncMock()
        bot.on_message(filters.text)(failing_handler)
        bot.on_error(error_handler)

        await bot._dispatch(self._make_raw("hello"))
        error_handler.assert_called_once()
        args = error_handler.call_args[0]
        assert isinstance(args[0], ValueError)
        assert isinstance(args[1], Message)

    @pytest.mark.asyncio
    async def test_dispatch_no_filter_matches_all(self):
        bot = WeChatBot(token="test")
        handler = AsyncMock()
        bot.on_message()(handler)  # No filter = match all

        await bot._dispatch(self._make_raw("anything"))
        handler.assert_called_once()


class TestCursorPersistence:
    def test_save_and_load_cursor(self, tmp_path):
        cursor_file = tmp_path / "cursor.json"
        bot = WeChatBot(token="test", cursor_file=cursor_file)

        bot._save_cursor("test_cursor_123")
        assert cursor_file.exists()

        loaded = bot._load_cursor()
        assert loaded == "test_cursor_123"

    def test_load_missing_cursor(self, tmp_path):
        bot = WeChatBot(token="test", cursor_file=tmp_path / "missing.json")
        assert bot._load_cursor() == ""


class TestBotLifecycle:
    @pytest.mark.asyncio
    async def test_start_without_auth_raises(self):
        bot = WeChatBot(token_file="/nonexistent/path.json")
        with pytest.raises(RuntimeError, match="not authenticated"):
            await bot.start()

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        bot = WeChatBot(token="test_token")
        await bot.start()
        assert bot.is_running
        await bot.stop()
        assert not bot.is_running
