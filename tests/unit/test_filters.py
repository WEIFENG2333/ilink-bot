"""Tests for the filter system."""

from ilink_bot.bot.filters import _Filters, command, contains, from_user, regex
from ilink_bot.models.messages import Message, WeChatMessage


def _make_msg(text: str = "hello", item_type: int = 1) -> Message:
    """Helper to create a Message for testing."""
    items = []
    if item_type == 1:
        items = [{"type": 1, "text_item": {"text": text}}]
    elif item_type == 2:
        items = [{"type": 2, "image_item": {"media": {}}}]
    elif item_type == 3:
        items = [{"type": 3, "voice_item": {"text": text}}]
    elif item_type == 4:
        items = [{"type": 4, "file_item": {"file_name": "test.pdf"}}]
    elif item_type == 5:
        items = [{"type": 5, "video_item": {}}]

    raw = WeChatMessage(
        message_id=1,
        from_user_id="user@im.wechat",
        item_list=items,
    )
    return Message(raw)


class TestBasicFilters:
    def test_text_filter(self):
        f = _Filters()
        assert f.text(_make_msg("hello", 1)) is True
        assert f.text(_make_msg("", 2)) is False

    def test_image_filter(self):
        f = _Filters()
        assert f.image(_make_msg("", 2)) is True
        assert f.image(_make_msg("hello", 1)) is False

    def test_voice_filter(self):
        f = _Filters()
        assert f.voice(_make_msg("", 3)) is True
        assert f.voice(_make_msg("hello", 1)) is False

    def test_file_filter(self):
        f = _Filters()
        assert f.file(_make_msg("", 4)) is True
        assert f.file(_make_msg("hello", 1)) is False

    def test_video_filter(self):
        f = _Filters()
        assert f.video(_make_msg("", 5)) is True
        assert f.video(_make_msg("hello", 1)) is False

    def test_all_filter(self):
        f = _Filters()
        assert f.all(_make_msg("hello")) is True
        assert f.all(_make_msg("", 2)) is True


class TestContainsFilter:
    def test_match(self):
        f = contains("hello")
        assert f(_make_msg("say hello world")) is True

    def test_no_match(self):
        f = contains("goodbye")
        assert f(_make_msg("hello world")) is False

    def test_empty(self):
        f = contains("")
        assert f(_make_msg("anything")) is True


class TestRegexFilter:
    def test_match(self):
        f = regex(r"\d{3}")
        assert f(_make_msg("code 123 here")) is True

    def test_no_match(self):
        f = regex(r"\d{3}")
        assert f(_make_msg("no numbers")) is False

    def test_case_insensitive(self):
        import re

        f = regex(r"hello", re.IGNORECASE)
        assert f(_make_msg("HELLO world")) is True


class TestCommandFilter:
    def test_exact_command(self):
        f = command("help")
        assert f(_make_msg("/help")) is True

    def test_command_with_args(self):
        f = command("help")
        assert f(_make_msg("/help topic")) is True

    def test_no_match(self):
        f = command("help")
        assert f(_make_msg("/start")) is False

    def test_partial_no_match(self):
        f = command("help")
        assert f(_make_msg("/helper")) is False


class TestFromUserFilter:
    def test_match(self):
        f = from_user("user@im.wechat")
        assert f(_make_msg("hello")) is True

    def test_no_match(self):
        f = from_user("other@im.wechat")
        assert f(_make_msg("hello")) is False


class TestFilterCombination:
    def test_and(self):
        f = _Filters()
        combined = f.text & contains("help")
        assert combined(_make_msg("/help")) is True
        assert combined(_make_msg("goodbye")) is False
        assert combined(_make_msg("", 2)) is False  # image

    def test_or(self):
        f = _Filters()
        combined = f.text | f.image
        assert combined(_make_msg("hello")) is True
        assert combined(_make_msg("", 2)) is True
        assert combined(_make_msg("", 5)) is False  # video

    def test_not(self):
        f = _Filters()
        combined = ~f.text
        assert combined(_make_msg("hello")) is False
        assert combined(_make_msg("", 2)) is True

    def test_complex_combination(self):
        f = _Filters()
        combined = f.text & (contains("help") | contains("info"))
        assert combined(_make_msg("need help")) is True
        assert combined(_make_msg("more info")) is True
        assert combined(_make_msg("random text")) is False

    def test_filter_repr(self):
        f = _Filters()
        combined = f.text & contains("help")
        assert "text" in repr(combined)
        assert "contains" in repr(combined)
