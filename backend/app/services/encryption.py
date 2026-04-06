from __future__ import annotations

from cryptography.fernet import Fernet

from app.config import settings


def encrypt_api_key(plaintext: str) -> str:
    if not settings.encryption_key:
        return plaintext
    f = Fernet(settings.encryption_key.encode())
    return f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    if not settings.encryption_key:
        return ciphertext
    f = Fernet(settings.encryption_key.encode())
    return f.decrypt(ciphertext.encode()).decode()
