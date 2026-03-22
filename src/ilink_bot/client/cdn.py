"""CDN media upload / download with AES-128-ECB encryption.

Handles the full lifecycle of media files exchanged via the WeChat iLink CDN:

- **Upload**: read file -> MD5 -> generate AES key & filekey -> call
  ``getuploadurl`` -> AES-ECB encrypt -> HTTP PUT to CDN -> return
  download parameters.
- **Download**: build CDN URL with ``encrypt_query_param`` -> HTTP GET ->
  decode AES key -> AES-ECB decrypt -> return plaintext bytes.

Encryption uses AES-128-ECB with PKCS7 padding (via the ``cryptography``
library).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

logger = logging.getLogger("ilink_bot.client.cdn")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AES_BLOCK_BITS = 128
_AES_BLOCK_BYTES = _AES_BLOCK_BITS // 8
_DEFAULT_CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"


# ---------------------------------------------------------------------------
# AES-128-ECB helpers
# ---------------------------------------------------------------------------


def aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    """Encrypt *data* with AES-128-ECB and PKCS7 padding.

    Parameters
    ----------
    data:
        Plaintext bytes to encrypt.
    key:
        16-byte AES key.

    Returns
    -------
    bytes
        Ciphertext including PKCS7 padding.
    """
    padder = PKCS7(_AES_BLOCK_BITS).padder()
    padded = padder.update(data) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """Decrypt AES-128-ECB ciphertext and strip PKCS7 padding.

    Parameters
    ----------
    data:
        Ciphertext bytes (must be a multiple of 16).
    key:
        16-byte AES key.

    Returns
    -------
    bytes
        Original plaintext.
    """
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()

    unpadder = PKCS7(_AES_BLOCK_BITS).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def aes_ecb_padded_size(plaintext_size: int) -> int:
    """Return the ciphertext size after PKCS7 padding.

    The formula accounts for the extra padding byte that is always appended::

        math.ceil((plaintext_size + 1) / 16) * 16

    Parameters
    ----------
    plaintext_size:
        Length of the original plaintext in bytes.

    Returns
    -------
    int
        Size of the padded (and therefore encrypted) output in bytes.
    """
    return math.ceil((plaintext_size + 1) / _AES_BLOCK_BYTES) * _AES_BLOCK_BYTES


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class UploadedMedia:
    """Result of a successful CDN upload."""

    filekey: str
    """Server-assigned file key used in subsequent API calls."""

    download_param: str
    """``encrypt_query_param`` value needed to build the download URL."""

    aes_key_hex: str
    """Hex-encoded AES key that was used to encrypt the file."""

    file_size: int
    """Size of the original plaintext file in bytes."""

    cipher_size: int
    """Size of the encrypted payload uploaded to the CDN."""


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def upload_media(
    http: httpx.AsyncClient,
    file_data: bytes,
    media_type: int,
    to_user_id: str,
    upload_url_getter: Callable[..., Awaitable[dict[str, Any]]],
    cdn_base_url: str = _DEFAULT_CDN_BASE,
) -> UploadedMedia:
    """Encrypt and upload a media file to the WeChat iLink CDN.

    Workflow
    --------
    1. Compute the MD5 digest of the raw file.
    2. Generate a random 16-byte AES key and a random 16-byte-hex file key.
    3. Call *upload_url_getter* (``client.get_upload_url``) to obtain an
       upload authorisation token.
    4. AES-ECB encrypt the file.
    5. HTTP PUT the ciphertext to the CDN.
    6. Extract ``x-encrypted-param`` from the response headers.

    Parameters
    ----------
    http:
        Async HTTP client used for the CDN PUT request.
    file_data:
        Raw file content.
    media_type:
        Media category — ``1`` IMAGE, ``2`` VIDEO, ``3`` FILE, ``4`` VOICE.
    to_user_id:
        Target user / chat ID.
    upload_url_getter:
        Async callable that returns the upload URL metadata.  Typically
        ``ILinkClient.get_upload_url``.
    cdn_base_url:
        CDN root URL.  Defaults to the production endpoint.

    Returns
    -------
    UploadedMedia
        Metadata about the uploaded file, including the download parameter
        and AES key.
    """
    file_size = len(file_data)
    file_md5 = hashlib.md5(file_data).hexdigest()

    # Random AES key (16 bytes) & file key (16 hex chars = 8 random bytes)
    aes_key = os.urandom(_AES_BLOCK_BYTES)
    aes_key_hex = aes_key.hex()
    filekey = os.urandom(_AES_BLOCK_BYTES).hex()

    cipher_size = aes_ecb_padded_size(file_size)

    # Obtain upload authorisation via the provided getter
    upload_info = await upload_url_getter(
        file_md5=file_md5,
        file_size=file_size,
        cipher_size=cipher_size,
        media_type=media_type,
        to_user_id=to_user_id,
        filekey=filekey,
        aes_key_hex=aes_key_hex,
    )

    # The getter returns a dict with an ``upload_param`` key.
    upload_param = upload_info["upload_param"]

    # Encrypt the file
    ciphertext = aes_ecb_encrypt(file_data, aes_key)

    # PUT to CDN
    upload_url = f"{cdn_base_url}/upload?encrypted_query_param={upload_param}&filekey={filekey}"

    logger.debug(
        "Uploading %d bytes (cipher %d) to CDN, filekey=%s",
        file_size,
        cipher_size,
        filekey,
    )

    resp = await http.put(
        upload_url,
        content=ciphertext,
        headers={"Content-Type": "application/octet-stream"},
    )
    resp.raise_for_status()

    download_param = resp.headers["x-encrypted-param"]

    logger.debug("Upload complete, download_param=%s", download_param)

    return UploadedMedia(
        filekey=filekey,
        download_param=download_param,
        aes_key_hex=aes_key_hex,
        file_size=file_size,
        cipher_size=cipher_size,
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _decode_aes_key(aes_key: str) -> bytes:
    """Decode an AES key supplied as hex or base64.

    The caller may provide the key in two forms:

    * **Hex** (32 characters, all hex digits) — preferred.
    * **Base64** (typically 24 characters) — fallback when the key comes from
      ``media.aes_key`` in older messages.

    Parameters
    ----------
    aes_key:
        The key string to decode.

    Returns
    -------
    bytes
        The raw 16-byte AES key.
    """
    cleaned = aes_key.strip()
    # Hex-encoded key is exactly 32 hex chars for 16 bytes
    try:
        raw = bytes.fromhex(cleaned)
        if len(raw) == _AES_BLOCK_BYTES:
            return raw
    except ValueError:
        pass

    # Fallback: base64
    raw = base64.b64decode(cleaned)
    if len(raw) != _AES_BLOCK_BYTES:
        msg = f"Decoded AES key has unexpected length {len(raw)} (expected {_AES_BLOCK_BYTES})"
        raise ValueError(msg)
    return raw


async def download_media(
    http: httpx.AsyncClient,
    encrypt_query_param: str,
    aes_key: str,
    cdn_base_url: str = _DEFAULT_CDN_BASE,
) -> bytes:
    """Download and decrypt a media file from the WeChat iLink CDN.

    Parameters
    ----------
    http:
        Async HTTP client.
    encrypt_query_param:
        Opaque query parameter that authorises the download.
    aes_key:
        AES key as a hex string (preferred) or base64 string.
    cdn_base_url:
        CDN root URL.  Defaults to the production endpoint.

    Returns
    -------
    bytes
        Decrypted file content.
    """
    url = f"{cdn_base_url}/download?encrypted_query_param={encrypt_query_param}"

    logger.debug("Downloading media from CDN")
    resp = await http.get(url)
    resp.raise_for_status()

    key = _decode_aes_key(aes_key)
    return aes_ecb_decrypt(resp.content, key)
