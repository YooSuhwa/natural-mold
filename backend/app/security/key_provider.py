"""Encryption key provider — parses ``ENCRYPTION_KEYS`` and exposes accessors.

The setting is a comma-separated list of 64-char hex keys. The first key is the
active (encrypting) key; all keys are candidates for decryption to support
seamless rotation.

Boot fails (raises ``RuntimeError``) if the setting is empty or any key is
malformed — there is no plaintext fallback.
"""

from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings
from app.security.cipher import (
    AES_KEY_LEN,
    IV_LEN,
    CipherKey,
    DecryptionError,
    InvalidKeyError,
    TenantKeyError,
)

logger = logging.getLogger(__name__)


def _parse_keys(raw: str | list[str]) -> list[CipherKey]:
    if isinstance(raw, str):
        candidates = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        candidates = [part.strip() for part in raw if str(part).strip()]
    if not candidates:
        raise RuntimeError(
            "ENCRYPTION_KEYS is empty — refusing to start. Configure one or more "
            "64-char hex keys (comma-separated) before booting."
        )
    keys: list[CipherKey] = []
    for idx, hex_str in enumerate(candidates):
        try:
            keys.append(CipherKey.from_hex(hex_str))
        except InvalidKeyError as exc:
            raise RuntimeError(f"ENCRYPTION_KEYS[{idx}] is invalid: {exc}") from exc
    return keys


@lru_cache(maxsize=1)
def get_keys() -> list[CipherKey]:
    """Return the parsed key list; cached after first call."""

    keys = _parse_keys(getattr(settings, "encryption_keys", "") or "")
    logger.info("loaded %d encryption key(s); active key_id=%s", len(keys), keys[0].key_id)
    return keys


def get_active_key() -> CipherKey:
    """Return the active (first) key used for new encryptions."""

    return get_keys()[0]


def get_active_key_id() -> str:
    return get_active_key().key_id


def reset_cache() -> None:
    """Clear the cached key list — used by tests that mutate ``settings``."""

    get_keys.cache_clear()


# -- Company data key wrapping (envelope encryption, B-16) -------------------

_WRAP_INFO = b"moldy-tenant-data-key-wrap-v1"


def _require_local_provider() -> None:
    if getattr(settings, "data_key_provider", "local") != "local":
        raise TenantKeyError("DATA_KEY_PROVIDER=transit is not available yet")


def _kek(key: CipherKey) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=AES_KEY_LEN, salt=None, info=_WRAP_INFO).derive(
        key.raw
    )


def _wrap_aad(tenant_id: object, key_id: str, version: int) -> bytes:
    # The key version is immutable per key (rotation adds a new key, rewrap keeps
    # the version), so binding it here cannot break rotation.
    return f"moldy-data-key|{tenant_id}|{key_id}|v{version}".encode()


def wrap_data_key(raw: bytes, *, tenant_id: object, key_id: str, version: int) -> tuple[str, str]:
    """Wrap a company data key with the active instance key → ``(wrapped, wrap_key_id)``."""

    _require_local_provider()
    active = get_active_key()
    nonce = os.urandom(IV_LEN)
    sealed = AESGCM(_kek(active)).encrypt(nonce, raw, _wrap_aad(tenant_id, key_id, version))
    return base64.b64encode(nonce + sealed).decode("ascii"), active.key_id


def unwrap_data_key(
    wrapped: str, *, wrap_key_id: str, tenant_id: object, key_id: str, version: int
) -> bytes:
    """Unwrap a company data key with the instance key that wrapped it."""

    _require_local_provider()
    instance_key = next((key for key in get_keys() if key.key_id == wrap_key_id), None)
    if instance_key is None:
        raise TenantKeyError("the instance key that wrapped this company key is not configured")
    try:
        blob = base64.b64decode(wrapped.encode("ascii"), validate=True)
        raw = AESGCM(_kek(instance_key)).decrypt(
            blob[:IV_LEN], blob[IV_LEN:], _wrap_aad(tenant_id, key_id, version)
        )
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError("company data key could not be unwrapped") from exc
    if len(raw) != AES_KEY_LEN:
        raise TenantKeyError("unwrapped company data key has an invalid length")
    return raw
