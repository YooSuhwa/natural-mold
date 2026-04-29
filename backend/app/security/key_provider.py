"""Encryption key provider — parses ``ENCRYPTION_KEYS`` and exposes accessors.

The setting is a comma-separated list of 64-char hex keys. The first key is the
active (encrypting) key; all keys are candidates for decryption to support
seamless rotation.

Boot fails (raises ``RuntimeError``) if the setting is empty or any key is
malformed — there is no plaintext fallback.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings
from app.security.cipher import CipherKey, InvalidKeyError

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
            raise RuntimeError(
                f"ENCRYPTION_KEYS[{idx}] is invalid: {exc}"
            ) from exc
    return keys


@lru_cache(maxsize=1)
def get_keys() -> list[CipherKey]:
    """Return the parsed key list; cached after first call."""

    keys = _parse_keys(getattr(settings, "encryption_keys", "") or "")
    logger.info(
        "loaded %d encryption key(s); active key_id=%s", len(keys), keys[0].key_id
    )
    return keys


def get_active_key() -> CipherKey:
    """Return the active (first) key used for new encryptions."""

    return get_keys()[0]


def get_active_key_id() -> str:
    return get_active_key().key_id


def reset_cache() -> None:
    """Clear the cached key list — used by tests that mutate ``settings``."""

    get_keys.cache_clear()
