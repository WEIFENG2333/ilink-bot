"""Tests for data models."""

from ilink_bot.models.messages import (
    BotToken,
    Message,
    MessageItemType,
    MessageState,
    MessageType,
    QRCode,
    QRCodeStatus,
    UpdatesResponse,
    WeChatMessage,
)


class TestEnums:
    def test_message_type_values(self):
        assert MessageType.NONE == 0
        assert MessageType.USER == 1
        assert MessageType.BOT == 2

    def test_message_item_type_values(self):
        assert MessageItemType.NONE == 0
        assert MessageItemType.TEXT == 1
        assert MessageItemType.IMAGE == 2
        assert MessageItemType.VOICE == 3
        assert MessageItemType.FILE == 4
        assert MessageItemType.VIDEO == 5

    def test_message_state_values(self):
        assert MessageState.NEW == 0
        assert MessageState.GENERATING == 1
        assert MessageState.FINISH == 2

    def test_qrcode_status_values(self):
        assert QRCodeStatus.WAIT == "wait"
        assert QRCodeStatus.SCANNED == "scaned"  # upstream typo
        assert QRCodeStatus.CONFIRMED == "confirmed"
        assert QRCodeStatus.EXPIRED == "expired"


class TestWeChatMessage:
    def test_parse_text_message(self):
        data = {
            "message_id": 12345,
            "from_user_id": "user123@im.wechat",
            "to_user_id": "bot456@im.bot",
            "message_type": 1,
            "message_state": 2,
            "create_time_ms": 1742000000000,
            "item_list": [{"type": 1, "text_item": {"text": "Hello bot!"}}],
            "context_token": "ctx_abc",
        }
        msg = WeChatMessage(**data)
        assert msg.message_id == 12345
        assert msg.from_user_id == "user123@im.wechat"
        assert msg.message_type == 1
        assert msg.context_token == "ctx_abc"
        assert len(msg.item_list) == 1
        assert msg.item_list[0].type == 1
        assert msg.item_list[0].text_item.text == "Hello bot!"

    def test_parse_empty_message(self):
        msg = WeChatMessage()
        assert msg.message_id is None
        assert msg.item_list is None

    def test_parse_image_message(self):
        data = {
            "item_list": [
                {
                    "type": 2,
                    "image_item": {
                        "media": {"encrypt_query_param": "abc", "aes_key": "key123"},
                        "mid_size": 1024,
                    },
                }
            ],
        }
        msg = WeChatMessage(**data)
        assert msg.item_list[0].type == 2
        assert msg.item_list[0].image_item.media.encrypt_query_param == "abc"
        assert msg.item_list[0].image_item.mid_size == 1024


class TestUpdatesResponse:
    def test_parse_success(self):
        data = {
            "ret": 0,
            "msgs": [
                {
                    "message_id": 1,
                    "from_user_id": "user@im.wechat",
                    "item_list": [{"type": 1, "text_item": {"text": "hi"}}],
                }
            ],
            "get_updates_buf": "cursor_abc",
        }
        resp = UpdatesResponse(**data)
        assert resp.ret == 0
        assert len(resp.msgs) == 1
        assert resp.get_updates_buf == "cursor_abc"

    def test_parse_error(self):
        data = {"ret": -14, "errcode": -14, "errmsg": "session expired"}
        resp = UpdatesResponse(**data)
        assert resp.ret == -14
        assert resp.errcode == -14
        assert len(resp.msgs) == 0

    def test_empty_response(self):
        resp = UpdatesResponse()
        assert resp.ret is None
        assert len(resp.msgs) == 0


class TestHighLevelMessage:
    def _make_raw(self, **kwargs) -> WeChatMessage:
        defaults = {
            "message_id": 1,
            "from_user_id": "sender@im.wechat",
            "to_user_id": "bot@im.bot",
            "create_time_ms": 1742000000000,
            "context_token": "ctx_1",
            "message_type": 1,
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
        }
        defaults.update(kwargs)
        return WeChatMessage(**defaults)

    def test_text_properties(self):
        msg = Message(self._make_raw())
        assert msg.id == 1
        assert msg.from_user == "sender@im.wechat"
        assert msg.to_user == "bot@im.bot"
        assert msg.text == "hello"
        assert msg.type == MessageItemType.TEXT
        assert msg.context_token == "ctx_1"
        assert msg.timestamp is not None

    def test_image_properties(self):
        raw = self._make_raw(
            item_list=[
                {
                    "type": 2,
                    "image_item": {"media": {"encrypt_query_param": "ep"}, "mid_size": 512},
                }
            ]
        )
        msg = Message(raw)
        assert msg.type == MessageItemType.IMAGE
        assert msg.text is None
        assert msg.image is not None
        assert msg.image.mid_size == 512

    def test_voice_to_text(self):
        raw = self._make_raw(item_list=[{"type": 3, "voice_item": {"text": "voice transcription"}}])
        msg = Message(raw)
        assert msg.type == MessageItemType.VOICE
        assert msg.text == "voice transcription"

    def test_empty_items(self):
        raw = self._make_raw(item_list=[])
        msg = Message(raw)
        assert msg.type == MessageItemType.NONE
        assert msg.text is None

    def test_from_user_name(self):
        msg = Message(self._make_raw(from_user_id="alice@im.wechat"))
        assert msg.from_user_name == "alice"

    def test_from_user_name_no_at(self):
        msg = Message(self._make_raw(from_user_id="alice"))
        assert msg.from_user_name == "alice"

    def test_quoted_message(self):
        raw = self._make_raw(
            item_list=[
                {
                    "type": 1,
                    "text_item": {"text": "reply text"},
                    "ref_msg": {"title": "original message"},
                }
            ]
        )
        msg = Message(raw)
        assert msg.text == "[引用: original message]\nreply text"
        assert msg.quoted_text == "original message"
        assert msg.ref_message is not None
        assert msg.ref_message.title == "original message"

    def test_no_quoted_message(self):
        msg = Message(self._make_raw())
        assert msg.quoted_text is None
        assert msg.ref_message is None

    def test_repr(self):
        msg = Message(self._make_raw())
        r = repr(msg)
        assert "Message" in r
        assert "sender@im.wechat" in r

    async def test_reply_without_client_raises(self):
        import pytest

        msg = Message(self._make_raw())
        with pytest.raises(RuntimeError, match="no associated ILinkClient"):
            await msg.reply("test")


class TestQRCode:
    def test_parse(self):
        qr = QRCode(qrcode="abc123", qrcode_img_content="https://example.com/qr")
        assert qr.qrcode == "abc123"
        assert qr.qrcode_img_content == "https://example.com/qr"


class TestBotToken:
    def test_serialize(self):
        bt = BotToken(token="tok_123", base_url="https://api.example.com", bot_id="bot1")
        data = bt.model_dump()
        assert data["token"] == "tok_123"
        assert data["base_url"] == "https://api.example.com"
