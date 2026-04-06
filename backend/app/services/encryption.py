from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)
_fernet: Fernet | None = None


def _get_fernet() -> Fernet | None:
    global _fernet
    if not settings.encryption_key:
        return None
    if _fernet is None:
        _fernet = Fernet(settings.encryption_key.encode())
    return _fernet


def encrypt_api_key(plaintext: str) -> str:
    f = _get_fernet()
    if not f:
        logger.warning("ENCRYPTION_KEY not set — API key stored in plaintext")
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    f = _get_fernet()
    if not f:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.warning("Failed to decrypt API key — returning as-is (likely legacy plaintext)")
        return ciphertext
