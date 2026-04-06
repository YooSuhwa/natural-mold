"""Tests for encryption service."""

from __future__ import annotations

from unittest.mock import patch

from cryptography.fernet import Fernet


class TestEncryption:
    def test_encrypt_decrypt_roundtrip_with_key(self):
        test_key = Fernet.generate_key().decode()
        with patch("app.services.encryption.settings") as mock_settings:
            mock_settings.encryption_key = test_key
            # Reset cached fernet instance
            import app.services.encryption as enc_mod

            enc_mod._fernet = None
            from app.services.encryption import decrypt_api_key, encrypt_api_key

            plaintext = "sk-test-api-key-12345"
            encrypted = encrypt_api_key(plaintext)
            assert encrypted != plaintext
            decrypted = decrypt_api_key(encrypted)
            assert decrypted == plaintext
            # Cleanup
            enc_mod._fernet = None

    def test_no_encryption_key_passthrough(self):
        with patch("app.services.encryption.settings") as mock_settings:
            mock_settings.encryption_key = ""
            import app.services.encryption as enc_mod

            enc_mod._fernet = None
            from app.services.encryption import decrypt_api_key, encrypt_api_key

            plaintext = "sk-test-key"
            assert encrypt_api_key(plaintext) == plaintext
            assert decrypt_api_key(plaintext) == plaintext
            enc_mod._fernet = None

    def test_invalid_token_fallback(self):
        """decrypt_api_key returns ciphertext as-is when it cannot be decrypted."""
        test_key = Fernet.generate_key().decode()
        with patch("app.services.encryption.settings") as mock_settings:
            mock_settings.encryption_key = test_key
            import app.services.encryption as enc_mod

            enc_mod._fernet = None
            from app.services.encryption import decrypt_api_key

            # A plaintext string that is not valid Fernet ciphertext
            plaintext = "sk-legacy-plaintext-key"
            result = decrypt_api_key(plaintext)
            assert result == plaintext
            enc_mod._fernet = None
