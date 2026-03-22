"""Layer 1 — Low-level iLink protocol client.

Handles all HTTP communication with the WeChat iLink server:
- Header construction (Authorization, X-WECHAT-UIN, AuthorizationType)
- QR-code login flow
- Long-poll ``getupdates``
- ``sendmessage`` / ``sendtyping`` / ``getconfig``
- CDN media upload / download (image, file, video, voice)
- Token persistence
- Per-user ``context_token`` caching
- Send-rate limiting (token bucket)

No business logic lives here — that belongs in Layer 2 (:mod:`ilink_bot.bot`).
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import struct
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ilink_bot.client.cdn import UploadedMedia, download_media, upload_media
from ilink_bot.client.rate_limiter import AsyncRateLimiter
from ilink_bot.models.messages import (
    BotInfo,
    BotToken,
    GetConfigResponse,
    MessageItemType,
    MessageState,
    MessageType,
    QRCode,
    QRCodeStatus,
    QRCodeStatusResponse,
    TypingStatus,
    UpdatesResponse,
    UploadMediaType,
)

logger = logging.getLogger("ilink_bot.client")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
DEFAULT_BOT_TYPE = "3"
CHANNEL_VERSION = "0.1.0"

DEFAULT_TOKEN_DIR = Path.home() / ".ilink-bot"
DEFAULT_TOKEN_FILE = DEFAULT_TOKEN_DIR / "token.json"

LONG_POLL_TIMEOUT = 35.0  # seconds
API_TIMEOUT = 15.0
QR_POLL_TIMEOUT = 35.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _random_wechat_uin() -> str:
    """Generate ``X-WECHAT-UIN`` header value: base64(str(random_uint32))."""
    raw = os.urandom(4)
    uint32 = struct.unpack(">I", raw)[0]
    return base64.b64encode(str(uint32).encode()).decode()


def _build_base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION}


# ---------------------------------------------------------------------------
# ILinkClient
# ---------------------------------------------------------------------------


class ILinkClient:
    """Async HTTP client for the WeChat iLink Bot protocol.

    Parameters
    ----------
    token:
        Bot token obtained via QR-code login.  Falls back to ``ILINK_TOKEN``
        env var, then the persisted *token_file*.
    base_url:
        iLink API base URL.  Falls back to ``ILINK_BASE_URL`` env var,
        then the default ``https://ilinkai.weixin.qq.com``.
    token_file:
        Path for persisting / loading the bot token.  Falls back to
        ``ILINK_TOKEN_FILE`` env var, then ``~/.ilink-bot/token.json``.
    send_rate:
        Token-bucket rate for ``sendmessage`` (tokens per second, default 1.0).
    send_burst:
        Token-bucket burst for ``sendmessage`` (default 3).

    Environment Variables
    ---------------------
    ``ILINK_TOKEN``
        Bot token (alternative to constructor *token* parameter).
    ``ILINK_BASE_URL``
        API base URL override.
    ``ILINK_TOKEN_FILE``
        Token file path override.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        base_url: str | None = None,
        token_file: str | Path | None = None,
        send_rate: float = 1.0,
        send_burst: int = 3,
    ) -> None:
        self._base_url = (base_url or os.environ.get("ILINK_BASE_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        )
        self._token: str | None = token or os.environ.get("ILINK_TOKEN")
        tf_env = os.environ.get("ILINK_TOKEN_FILE")
        self._token_file = (
            Path(token_file) if token_file else Path(tf_env) if tf_env else DEFAULT_TOKEN_FILE
        )
        self._bot_id: str = ""
        self._user_id: str = ""
        self._http: httpx.AsyncClient | None = None

        # Per-user context_token cache (user_id → latest context_token)
        self._context_tokens: dict[str, str] = {}

        # Rate limiter for sendmessage (token bucket)
        self._send_limiter = AsyncRateLimiter(rate=send_rate, burst=send_burst)

        # Try loading persisted token if none supplied
        if not self._token:
            self._load_token()

    # -- HTTP session management -------------------------------------------

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0, read=LONG_POLL_TIMEOUT + 5, write=5.0, pool=10.0
                ),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
            )
        return self._http

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        http = await self._ensure_http()
        url = f"{self._base_url}/{endpoint}"
        raw_body = json.dumps(body, ensure_ascii=False)
        headers = self._headers()
        headers["Content-Length"] = str(len(raw_body.encode()))

        effective_timeout = timeout or API_TIMEOUT
        logger.debug("POST %s (timeout=%.1fs)", endpoint, effective_timeout)

        resp = await http.post(
            url,
            content=raw_body,
            headers=headers,
            timeout=effective_timeout,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        http = await self._ensure_http()
        url = f"{self._base_url}/{endpoint}"
        headers = extra_headers or {}
        resp = await http.get(url, params=params, headers=headers, timeout=timeout or API_TIMEOUT)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    # -- Token persistence -------------------------------------------------

    def _load_token(self) -> None:
        if not self._token_file.exists():
            return
        try:
            data = json.loads(self._token_file.read_text())
            self._token = data.get("token")
            self._base_url = data.get("base_url", self._base_url).rstrip("/")
            self._bot_id = data.get("bot_id", "")
            self._user_id = data.get("user_id", "")
            logger.info("Loaded token from %s (bot_id=%s)", self._token_file, self._bot_id)
        except Exception:
            logger.warning("Failed to load token from %s", self._token_file, exc_info=True)

    def _save_token(
        self, token: str, base_url: str = "", bot_id: str = "", user_id: str = ""
    ) -> None:
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        data = BotToken(
            token=token,
            base_url=base_url or self._base_url,
            bot_id=bot_id,
            user_id=user_id,
            saved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self._token_file.write_text(data.model_dump_json(indent=2))
        # Restrict permissions (best-effort)
        with contextlib.suppress(OSError):
            self._token_file.chmod(0o600)
        logger.info("Token saved to %s", self._token_file)

    # -- Properties --------------------------------------------------------

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    def get_bot_info(self) -> BotInfo:
        return BotInfo(
            bot_id=self._bot_id,
            user_id=self._user_id,
            connected=self.is_authenticated,
            base_url=self._base_url,
        )

    # =====================================================================
    # QR-code login flow
    # =====================================================================

    async def get_qrcode(self, bot_type: str = DEFAULT_BOT_TYPE) -> QRCode:
        """Fetch a new QR code for scanning with the WeChat app."""
        data = await self._get(
            "ilink/bot/get_bot_qrcode",
            params={"bot_type": bot_type},
            timeout=10.0,
        )
        return QRCode(**data)

    async def poll_qrcode_status(self, qrcode: str) -> QRCodeStatusResponse:
        """Long-poll the QR-code login status (server holds up to ~35 s)."""
        data = await self._get(
            "ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            extra_headers={"iLink-App-ClientVersion": "1"},
            timeout=QR_POLL_TIMEOUT + 5,
        )
        return QRCodeStatusResponse(**data)

    async def login(self, bot_type: str = DEFAULT_BOT_TYPE) -> BotToken:
        """Full QR-code login flow: get QR → poll until confirmed → persist token.

        Returns the :class:`BotToken` on success.

        Raises
        ------
        TimeoutError
            If the user does not scan within ~5 minutes.
        RuntimeError
            If the server returns an unexpected status.
        """
        qr = await self.get_qrcode(bot_type)
        logger.info("QR code ready: %s", qr.qrcode_img_content[:80])

        deadline = time.monotonic() + 300  # 5 min
        while time.monotonic() < deadline:
            try:
                status = await self.poll_qrcode_status(qr.qrcode)
            except (httpx.TimeoutException, httpx.ReadTimeout):
                continue  # long-poll timeout is normal

            if status.status == QRCodeStatus.WAIT:
                continue
            if status.status == QRCodeStatus.SCANNED:
                logger.info("QR code scanned, waiting for confirmation...")
                continue
            if status.status == QRCodeStatus.EXPIRED:
                logger.info("QR code expired, refreshing...")
                qr = await self.get_qrcode(bot_type)
                continue
            if status.status == QRCodeStatus.CONFIRMED:
                if not status.bot_token or not status.ilink_bot_id:
                    raise RuntimeError(
                        "Login confirmed but server returned no bot_token/ilink_bot_id"
                    )
                base_url = (status.baseurl or self._base_url).rstrip("/")
                self._token = status.bot_token
                self._base_url = base_url
                self._bot_id = status.ilink_bot_id
                self._user_id = status.ilink_user_id or ""
                self._save_token(status.bot_token, base_url, self._bot_id, self._user_id)
                logger.info("Login successful! bot_id=%s", self._bot_id)
                return BotToken(
                    token=status.bot_token,
                    base_url=base_url,
                    bot_id=self._bot_id,
                    user_id=self._user_id,
                )

        raise TimeoutError("QR code login timed out (5 min)")

    # =====================================================================
    # Messaging APIs
    # =====================================================================

    async def get_updates(self, cursor: str = "") -> UpdatesResponse:
        """Long-poll for new messages.

        Automatically caches ``context_token`` from inbound user messages so
        that subsequent :meth:`send_text` / :meth:`send_image` calls can
        auto-resolve the token if the caller does not provide one.

        Parameters
        ----------
        cursor:
            Opaque ``get_updates_buf`` from the previous response.
            Pass ``""`` on the first call.
        """
        try:
            data = await self._post(
                "ilink/bot/getupdates",
                {"get_updates_buf": cursor, "base_info": _build_base_info()},
                timeout=LONG_POLL_TIMEOUT + 5,
            )
            resp = UpdatesResponse(**data)
        except httpx.TimeoutException:
            # Long-poll timeout is expected — return empty response so caller can retry
            logger.debug("getUpdates: long-poll timeout (normal)")
            return UpdatesResponse(ret=0, msgs=[], get_updates_buf=cursor)

        # Auto-cache context_tokens from inbound messages
        for msg in resp.msgs:
            if msg.from_user_id and msg.context_token:
                self._context_tokens[msg.from_user_id] = msg.context_token

        return resp

    # -- context_token helpers ---------------------------------------------

    def get_context_token(self, user_id: str) -> str | None:
        """Return the cached ``context_token`` for *user_id*, if any."""
        return self._context_tokens.get(user_id)

    def set_context_token(self, user_id: str, token: str) -> None:
        """Manually set the ``context_token`` for *user_id*."""
        self._context_tokens[user_id] = token

    def _resolve_context_token(self, to_user_id: str, explicit: str | None) -> str | None:
        """Return *explicit* if given, else look up the cached token."""
        if explicit:
            return explicit
        return self._context_tokens.get(to_user_id)

    # =====================================================================
    # Messaging APIs
    # =====================================================================

    async def _send_message(self, body: dict[str, Any]) -> dict[str, Any]:
        """Rate-limited wrapper around the ``sendmessage`` endpoint."""
        await self._send_limiter.acquire()
        return await self._post("ilink/bot/sendmessage", body)

    async def send_text(
        self,
        to_user_id: str,
        text: str,
        *,
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message.

        If *context_token* is ``None``, the client automatically uses the
        cached token from the most recent inbound message from *to_user_id*.
        """
        ctx = self._resolve_context_token(to_user_id, context_token)
        client_id = f"ilink-bot-{uuid.uuid4().hex[:12]}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT.value,
                "message_state": MessageState.FINISH.value,
                "item_list": [{"type": MessageItemType.TEXT.value, "text_item": {"text": text}}],
                **({"context_token": ctx} if ctx else {}),
            },
            "base_info": _build_base_info(),
        }
        result = await self._send_message(body)
        return {"message_id": client_id, **result}

    async def send_typing(
        self,
        to_user_id: str,
        typing_ticket: str,
        *,
        status: TypingStatus = TypingStatus.TYPING,
    ) -> dict[str, Any]:
        """Send a typing indicator."""
        body = {
            "ilink_user_id": to_user_id,
            "typing_ticket": typing_ticket,
            "status": status.value,
            "base_info": _build_base_info(),
        }
        return await self._post("ilink/bot/sendtyping", body, timeout=10.0)

    async def get_config(
        self,
        user_id: str,
        context_token: str | None = None,
    ) -> GetConfigResponse:
        """Fetch bot config (includes ``typing_ticket``) for a user."""
        ctx = self._resolve_context_token(user_id, context_token)
        body: dict[str, Any] = {
            "ilink_user_id": user_id,
            "base_info": _build_base_info(),
        }
        if ctx:
            body["context_token"] = ctx
        data = await self._post("ilink/bot/getconfig", body, timeout=10.0)
        return GetConfigResponse(**data)

    # =====================================================================
    # CDN — upload URL acquisition
    # =====================================================================

    async def get_upload_url(
        self,
        *,
        file_md5: str,
        file_size: int,
        cipher_size: int,
        media_type: int,
        to_user_id: str,
        filekey: str,
        aes_key_hex: str,
    ) -> dict[str, Any]:
        """Call ``getuploadurl`` to obtain a CDN upload authorisation.

        This method is mainly used internally by :func:`upload_media` but
        is exposed for advanced use-cases.
        """
        body: dict[str, Any] = {
            "to_user_id": to_user_id,
            "media_type": media_type,
            "file_md5": file_md5,
            "file_size": file_size,
            "cipher_size": cipher_size,
            "filekey": filekey,
            "aes_key": aes_key_hex,
            "base_info": _build_base_info(),
        }
        return await self._post("ilink/bot/getuploadurl", body, timeout=15.0)

    # =====================================================================
    # Media send helpers (image / file / video / voice)
    # =====================================================================

    async def _upload_and_build_item(
        self,
        file_data: bytes,
        media_type: UploadMediaType,
        to_user_id: str,
    ) -> UploadedMedia:
        """Upload *file_data* to the CDN and return the upload result."""
        http = await self._ensure_http()
        return await upload_media(
            http=http,
            file_data=file_data,
            media_type=media_type.value,
            to_user_id=to_user_id,
            upload_url_getter=self.get_upload_url,
        )

    async def send_image(
        self,
        to_user_id: str,
        image_data: bytes,
        *,
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Upload and send an image message.

        Parameters
        ----------
        to_user_id:
            Recipient WeChat user ID.
        image_data:
            Raw image bytes (JPEG / PNG / etc.).
        context_token:
            Optional; auto-resolved from cache if omitted.
        """
        ctx = self._resolve_context_token(to_user_id, context_token)
        uploaded = await self._upload_and_build_item(image_data, UploadMediaType.IMAGE, to_user_id)
        client_id = f"ilink-bot-{uuid.uuid4().hex[:12]}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT.value,
                "message_state": MessageState.FINISH.value,
                "item_list": [
                    {
                        "type": MessageItemType.IMAGE.value,
                        "image_item": {
                            "media": {
                                "encrypt_query_param": uploaded.download_param,
                                "aes_key": uploaded.aes_key_hex,
                                "encrypt_type": 1,
                            },
                        },
                    }
                ],
                **({"context_token": ctx} if ctx else {}),
            },
            "base_info": _build_base_info(),
        }
        result = await self._send_message(body)
        return {"message_id": client_id, **result}

    async def send_file(
        self,
        to_user_id: str,
        file_data: bytes,
        file_name: str,
        *,
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Upload and send a file message.

        Parameters
        ----------
        to_user_id:
            Recipient WeChat user ID.
        file_data:
            Raw file bytes.
        file_name:
            Display file name.
        context_token:
            Optional; auto-resolved from cache if omitted.
        """
        import hashlib

        ctx = self._resolve_context_token(to_user_id, context_token)
        uploaded = await self._upload_and_build_item(file_data, UploadMediaType.FILE, to_user_id)
        client_id = f"ilink-bot-{uuid.uuid4().hex[:12]}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT.value,
                "message_state": MessageState.FINISH.value,
                "item_list": [
                    {
                        "type": MessageItemType.FILE.value,
                        "file_item": {
                            "media": {
                                "encrypt_query_param": uploaded.download_param,
                                "aes_key": uploaded.aes_key_hex,
                                "encrypt_type": 1,
                            },
                            "file_name": file_name,
                            "md5": hashlib.md5(file_data).hexdigest(),
                            "len": str(uploaded.file_size),
                        },
                    }
                ],
                **({"context_token": ctx} if ctx else {}),
            },
            "base_info": _build_base_info(),
        }
        result = await self._send_message(body)
        return {"message_id": client_id, **result}

    async def send_video(
        self,
        to_user_id: str,
        video_data: bytes,
        *,
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Upload and send a video message."""
        ctx = self._resolve_context_token(to_user_id, context_token)
        uploaded = await self._upload_and_build_item(video_data, UploadMediaType.VIDEO, to_user_id)
        client_id = f"ilink-bot-{uuid.uuid4().hex[:12]}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT.value,
                "message_state": MessageState.FINISH.value,
                "item_list": [
                    {
                        "type": MessageItemType.VIDEO.value,
                        "video_item": {
                            "media": {
                                "encrypt_query_param": uploaded.download_param,
                                "aes_key": uploaded.aes_key_hex,
                                "encrypt_type": 1,
                            },
                            "video_size": uploaded.file_size,
                        },
                    }
                ],
                **({"context_token": ctx} if ctx else {}),
            },
            "base_info": _build_base_info(),
        }
        result = await self._send_message(body)
        return {"message_id": client_id, **result}

    async def send_voice(
        self,
        to_user_id: str,
        voice_data: bytes,
        *,
        context_token: str | None = None,
    ) -> dict[str, Any]:
        """Upload and send a voice message."""
        ctx = self._resolve_context_token(to_user_id, context_token)
        uploaded = await self._upload_and_build_item(voice_data, UploadMediaType.VOICE, to_user_id)
        client_id = f"ilink-bot-{uuid.uuid4().hex[:12]}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": client_id,
                "message_type": MessageType.BOT.value,
                "message_state": MessageState.FINISH.value,
                "item_list": [
                    {
                        "type": MessageItemType.VOICE.value,
                        "voice_item": {
                            "media": {
                                "encrypt_query_param": uploaded.download_param,
                                "aes_key": uploaded.aes_key_hex,
                                "encrypt_type": 1,
                            },
                        },
                    }
                ],
                **({"context_token": ctx} if ctx else {}),
            },
            "base_info": _build_base_info(),
        }
        result = await self._send_message(body)
        return {"message_id": client_id, **result}

    # =====================================================================
    # CDN — download helper
    # =====================================================================

    async def download_media(
        self,
        encrypt_query_param: str,
        aes_key: str,
    ) -> bytes:
        """Download and decrypt a media file from the CDN.

        Parameters
        ----------
        encrypt_query_param:
            Value from ``media.encrypt_query_param`` in the message.
        aes_key:
            AES key (hex or base64) from the message's ``media.aes_key``.
        """
        http = await self._ensure_http()
        return await download_media(http, encrypt_query_param, aes_key)

    # =====================================================================
    # Context manager support
    # =====================================================================

    async def __aenter__(self) -> ILinkClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
