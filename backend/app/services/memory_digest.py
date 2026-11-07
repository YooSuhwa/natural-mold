"""A memory text's keyed digest (memory policy plan 15, 5.7).

Per company, derived from the active master key, so a short value (a phone number) cannot be
reversed by hashing candidates without the key, and two companies never share a digest.
Rotating the master key changes every digest: erased-text blocks made before the rotation
stop matching (stated, accepted).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.security import key_provider
from app.services.memory_checks import content_fingerprint


def _key(tenant_id: uuid.UUID) -> bytes:
    info = f"moldy-memory-digest|{tenant_id}".encode()
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
        key_provider.get_active_key().raw
    )


def memory_digest(tenant_id: uuid.UUID, text: str) -> str:
    """HMAC-SHA256 of the normalized text (``content_fingerprint``), hex."""
    message = content_fingerprint(text).encode()
    return hmac.new(_key(tenant_id), message, hashlib.sha256).hexdigest()


__all__ = ["memory_digest"]
