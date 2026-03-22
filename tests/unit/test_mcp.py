"""Tests for MCP server helper functions and tool logic."""

from __future__ import annotations

from ilink_bot.models.messages import WeChatMessage


class TestExtractText:
    """Test the _extract_text helper."""

    def _extract(self, msg: WeChatMessage) -> str:
        from ilink_bot.mcp.server import _extract_text

        return _extract_text(msg)

    def test_text_message(self):
        msg = WeChatMessage(item_list=[{"type": 1, "text_item": {"text": "hello"}}])
        assert self._extract(msg) == "hello"

    def test_voice_message(self):
        msg = WeChatMessage(item_list=[{"type": 3, "voice_item": {"text": "voice text"}}])
        assert self._extract(msg) == "voice text"

    def test_empty_items(self):
        msg = WeChatMessage(item_list=[])
        assert self._extract(msg) == ""

    def test_none_items(self):
        msg = WeChatMessage(item_list=None)
        assert self._extract(msg) == ""

    def test_image_returns_empty(self):
        msg = WeChatMessage(item_list=[{"type": 2}])
        assert self._extract(msg) == ""

    def test_quoted_message(self):
        msg = WeChatMessage(
            item_list=[
                {
                    "type": 1,
                    "text_item": {"text": "reply"},
                    "ref_msg": {"title": "original"},
                }
            ]
        )
        result = self._extract(msg)
        assert "[引用: original]" in result
        assert "reply" in result


class TestExtractType:
    """Test the _extract_type helper."""

    def _extract(self, msg: WeChatMessage) -> str:
        from ilink_bot.mcp.server import _extract_type

        return _extract_type(msg)

    def test_text(self):
        msg = WeChatMessage(item_list=[{"type": 1, "text_item": {"text": "hi"}}])
        assert self._extract(msg) == "text"

    def test_image(self):
        msg = WeChatMessage(item_list=[{"type": 2}])
        assert self._extract(msg) == "image"

    def test_voice(self):
        msg = WeChatMessage(item_list=[{"type": 3}])
        assert self._extract(msg) == "voice"

    def test_file(self):
        msg = WeChatMessage(item_list=[{"type": 4}])
        assert self._extract(msg) == "file"

    def test_video(self):
        msg = WeChatMessage(item_list=[{"type": 5}])
        assert self._extract(msg) == "video"

    def test_empty(self):
        msg = WeChatMessage(item_list=[])
        assert self._extract(msg) == "none"

    def test_unknown_type(self):
        msg = WeChatMessage(item_list=[{"type": 99}])
        assert self._extract(msg) == "unknown"
