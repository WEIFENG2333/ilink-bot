"""Tests for CDN media encryption / decryption."""

from __future__ import annotations

import os

import pytest

from ilink_bot.client.cdn import (
    _decode_aes_key,
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    aes_ecb_padded_size,
)


class TestAESRoundTrip:
    def test_encrypt_decrypt_short(self):
        key = os.urandom(16)
        plaintext = b"Hello, WeChat!"
        ciphertext = aes_ecb_encrypt(plaintext, key)
        assert ciphertext != plaintext
        assert aes_ecb_decrypt(ciphertext, key) == plaintext

    def test_encrypt_decrypt_exact_block(self):
        key = os.urandom(16)
        plaintext = b"0123456789abcdef"  # exactly 16 bytes
        ciphertext = aes_ecb_encrypt(plaintext, key)
        assert aes_ecb_decrypt(ciphertext, key) == plaintext

    def test_encrypt_decrypt_empty(self):
        key = os.urandom(16)
        ciphertext = aes_ecb_encrypt(b"", key)
        assert aes_ecb_decrypt(ciphertext, key) == b""

    def test_encrypt_decrypt_large(self):
        key = os.urandom(16)
        plaintext = os.urandom(10_000)
        ciphertext = aes_ecb_encrypt(plaintext, key)
        assert aes_ecb_decrypt(ciphertext, key) == plaintext

    def test_ciphertext_is_block_aligned(self):
        key = os.urandom(16)
        for size in [0, 1, 15, 16, 17, 31, 32, 100]:
            ct = aes_ecb_encrypt(os.urandom(size) if size else b"", key)
            assert len(ct) % 16 == 0


class TestPaddedSize:
    def test_known_values(self):
        assert aes_ecb_padded_size(0) == 16
        assert aes_ecb_padded_size(1) == 16
        assert aes_ecb_padded_size(15) == 16
        assert aes_ecb_padded_size(16) == 32  # PKCS7 always adds at least 1 byte
        assert aes_ecb_padded_size(17) == 32

    def test_matches_actual_ciphertext_size(self):
        key = os.urandom(16)
        for size in [0, 1, 15, 16, 17, 31, 32, 100, 1000]:
            data = os.urandom(size) if size else b""
            ct = aes_ecb_encrypt(data, key)
            assert len(ct) == aes_ecb_padded_size(size)


class TestDecodeAESKey:
    def test_hex_key(self):
        key = os.urandom(16)
        hex_key = key.hex()
        assert _decode_aes_key(hex_key) == key

    def test_base64_key(self):
        import base64

        key = os.urandom(16)
        b64_key = base64.b64encode(key).decode()
        assert _decode_aes_key(b64_key) == key

    def test_hex_key_with_whitespace(self):
        key = os.urandom(16)
        assert _decode_aes_key(f"  {key.hex()}  ") == key

    def test_invalid_length_raises(self):
        import base64

        bad_key = base64.b64encode(b"too_short").decode()
        with pytest.raises(ValueError, match="unexpected length"):
            _decode_aes_key(bad_key)
