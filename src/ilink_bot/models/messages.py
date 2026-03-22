"""Strongly-typed data models for the iLink protocol.

All models mirror the Protobuf-over-JSON wire format used by the WeChat iLink API.
Fields use ``snake_case`` to match the upstream JSON keys exactly.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ilink_bot.client.client import ILinkClient


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MessageType(int, enum.Enum):
    """Who sent the message."""

    NONE = 0
    USER = 1
    BOT = 2


class MessageItemType(int, enum.Enum):
    """Content type inside a single message item."""

    NONE = 0
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class MessageState(int, enum.Enum):
    """Delivery state of a message."""

    NEW = 0
    GENERATING = 1
    FINISH = 2


class UploadMediaType(int, enum.Enum):
    """CDN upload media type identifier."""

    IMAGE = 1
    VIDEO = 2
    FILE = 3
    VOICE = 4


class TypingStatus(int, enum.Enum):
    """Typing indicator status."""

    TYPING = 1
    CANCEL = 2


class QRCodeStatus(str, enum.Enum):
    """Status returned by ``get_qrcode_status``."""

    WAIT = "wait"
    SCANNED = "scaned"  # NOTE: upstream typo kept intentionally
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Protocol sub-structures
# ---------------------------------------------------------------------------

class BaseInfo(BaseModel):
    """Metadata attached to every outgoing API request."""

    channel_version: str = ""


class CDNMedia(BaseModel):
    """CDN media reference for encrypted file downloads."""

    encrypt_query_param: str | None = None
    aes_key: str | None = None
    encrypt_type: int | None = None


class TextItem(BaseModel):
    text: str | None = None


class ImageItem(BaseModel):
    media: CDNMedia | None = None
    thumb_media: CDNMedia | None = None
    aeskey: str | None = None
    url: str | None = None
    mid_size: int | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None
    hd_size: int | None = None


class VoiceItem(BaseModel):
    media: CDNMedia | None = None
    encode_type: int | None = None
    bits_per_sample: int | None = None
    sample_rate: int | None = None
    playtime: int | None = None
    text: str | None = None


class FileItem(BaseModel):
    media: CDNMedia | None = None
    file_name: str | None = None
    md5: str | None = None
    len: str | None = None


class VideoItem(BaseModel):
    media: CDNMedia | None = None
    video_size: int | None = None
    play_length: int | None = None
    video_md5: str | None = None
    thumb_media: CDNMedia | None = None
    thumb_size: int | None = None
    thumb_height: int | None = None
    thumb_width: int | None = None


class RefMessage(BaseModel):
    """Quoted / referenced message inside a message item."""

    message_item: MessageItem | None = None
    title: str | None = None


class MessageItem(BaseModel):
    """A single content element within a :class:`WeChatMessage`."""

    type: int | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    is_completed: bool | None = None
    msg_id: str | None = None
    ref_msg: RefMessage | None = None
    text_item: TextItem | None = None
    image_item: ImageItem | None = None
    voice_item: VoiceItem | None = None
    file_item: FileItem | None = None
    video_item: VideoItem | None = None


# Allow forward references
RefMessage.model_rebuild()


# ---------------------------------------------------------------------------
# Wire-level message (matches upstream ``WeixinMessage``)
# ---------------------------------------------------------------------------

class WeChatMessage(BaseModel):
    """Raw message as returned by ``getupdates``."""

    seq: int | None = None
    message_id: int | None = None
    from_user_id: str | None = None
    to_user_id: str | None = None
    client_id: str | None = None
    create_time_ms: int | None = None
    update_time_ms: int | None = None
    delete_time_ms: int | None = None
    session_id: str | None = None
    group_id: str | None = None
    message_type: int | None = None
    message_state: int | None = None
    item_list: list[MessageItem] | None = None
    context_token: str | None = None


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

class GetUpdatesRequest(BaseModel):
    get_updates_buf: str = ""
    base_info: BaseInfo | None = None


class UpdatesResponse(BaseModel):
    """Response from ``getupdates``."""

    ret: int | None = None
    errcode: int | None = None
    errmsg: str | None = None
    msgs: list[WeChatMessage] = Field(default_factory=list)
    get_updates_buf: str | None = None
    longpolling_timeout_ms: int | None = None


class SendMessageRequest(BaseModel):
    """Wrapper for ``sendmessage``."""

    msg: WeChatMessage | None = None
    base_info: BaseInfo | None = None


class QRCode(BaseModel):
    """QR code returned by ``get_bot_qrcode``."""

    qrcode: str = ""
    qrcode_img_content: str = ""


class QRCodeStatusResponse(BaseModel):
    """Response from ``get_qrcode_status``."""

    status: QRCodeStatus = QRCodeStatus.WAIT
    bot_token: str | None = None
    ilink_bot_id: str | None = None
    baseurl: str | None = None
    ilink_user_id: str | None = None


class BotToken(BaseModel):
    """Persisted authentication credentials."""

    token: str
    base_url: str = ""
    bot_id: str = ""
    user_id: str = ""
    saved_at: str = ""


class BotInfo(BaseModel):
    """Runtime information about the connected bot."""

    bot_id: str = ""
    user_id: str = ""
    connected: bool = False
    base_url: str = ""


class GetConfigResponse(BaseModel):
    ret: int | None = None
    errmsg: str | None = None
    typing_ticket: str | None = None


class SendTypingRequest(BaseModel):
    ilink_user_id: str | None = None
    typing_ticket: str | None = None
    status: int | None = None
    base_info: BaseInfo | None = None


# ---------------------------------------------------------------------------
# High-level ``Message`` — the primary object developers interact with
# ---------------------------------------------------------------------------

class Message:
    """Developer-friendly message object with convenience methods.

    Wraps a raw :class:`WeChatMessage` and provides typed accessors and a
    ``reply()`` shortcut so handlers never touch the low-level protocol.
    """

    def __init__(self, raw: WeChatMessage, *, client: ILinkClient | None = None) -> None:
        self._raw = raw
        self._client = client

    # -- identity ----------------------------------------------------------

    @property
    def id(self) -> int | None:
        return self._raw.message_id

    @property
    def from_user(self) -> str:
        return self._raw.from_user_id or ""

    @property
    def to_user(self) -> str:
        return self._raw.to_user_id or ""

    @property
    def context_token(self) -> str | None:
        return self._raw.context_token

    @property
    def session_id(self) -> str | None:
        return self._raw.session_id

    # -- timestamps --------------------------------------------------------

    @property
    def timestamp(self) -> datetime | None:
        ts = self._raw.create_time_ms
        if ts is None:
            return None
        return datetime.fromtimestamp(ts / 1000)

    # -- content helpers ---------------------------------------------------

    @property
    def type(self) -> MessageItemType:
        """Type of the *first* content item (convenience)."""
        items = self._raw.item_list
        if not items:
            return MessageItemType.NONE
        return MessageItemType(items[0].type or 0)

    @property
    def text(self) -> str | None:
        """Extract text from the first TEXT item, or voice-to-text."""
        for item in self._raw.item_list or []:
            if item.type == MessageItemType.TEXT and item.text_item:
                return item.text_item.text
            if item.type == MessageItemType.VOICE and item.voice_item and item.voice_item.text:
                return item.voice_item.text
        return None

    @property
    def image(self) -> ImageItem | None:
        for item in self._raw.item_list or []:
            if item.type == MessageItemType.IMAGE:
                return item.image_item
        return None

    @property
    def voice(self) -> VoiceItem | None:
        for item in self._raw.item_list or []:
            if item.type == MessageItemType.VOICE:
                return item.voice_item
        return None

    @property
    def file(self) -> FileItem | None:
        for item in self._raw.item_list or []:
            if item.type == MessageItemType.FILE:
                return item.file_item
        return None

    @property
    def video(self) -> VideoItem | None:
        for item in self._raw.item_list or []:
            if item.type == MessageItemType.VIDEO:
                return item.video_item
        return None

    @property
    def items(self) -> list[MessageItem]:
        return self._raw.item_list or []

    @property
    def raw(self) -> WeChatMessage:
        """Access the underlying protocol message."""
        return self._raw

    # -- reply shortcut ----------------------------------------------------

    async def reply(self, content: str) -> dict[str, Any]:
        """Reply to this message with a text string.

        Automatically uses the correct ``context_token`` and ``to_user_id``.
        """
        if self._client is None:
            raise RuntimeError("Message has no associated ILinkClient; cannot reply.")
        return await self._client.send_text(
            to_user_id=self.from_user,
            text=content,
            context_token=self.context_token,
        )

    def __repr__(self) -> str:
        preview = (self.text or "")[:40]
        return f"<Message id={self.id} from={self.from_user!r} type={self.type.name} text={preview!r}>"
