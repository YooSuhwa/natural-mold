"""Tests for encryption service."""

from __future__ import annotations

from unittest.mock import patch


class TestEncryption:
    def test_encrypt_decrypt_roundtrip_with_key(self):
        # Valid Fernet key (base64-encoded 32 bytes)
        test_key = "dGVzdGtleTEyMzQ1Njc4OTAxMjM0NTY3ODkwMTIzNDU="
        with patch("app.services.encryption.settings") as mock_settings:
            mock_settings.encryption_key = test_key
            from app.services.encryption import decrypt_api_key, encrypt_api_key

            plaintext = "sk-test-api-key-12345"
            encrypted = encrypt_api_key(plaintext)
            assert encrypted != plaintext
            decrypted = decrypt_api_key(encrypted)
            assert decrypted == plaintext

    def test_no_encryption_key_passthrough(self):
        with patch("app.services.encryption.settings") as mock_settings:
            mock_settings.encryption_key = ""
            from app.services.encryption import decrypt_api_key, encrypt_api_key

            plaintext = "sk-test-key"
            assert encrypt_api_key(plaintext) == plaintext
            assert decrypt_api_key(plaintext) == plaintext
