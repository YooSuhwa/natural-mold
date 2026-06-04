from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from hashlib import sha256

from app.config import settings

KEY_PREFIX = "moldy_sk"


@dataclass(frozen=True)
class GeneratedApiKey:
    key_id: str
    secret: str
    cleartext: str
    secret_hash: str
    prefix: str
    last_four: str


def _signing_secret() -> str:
    return settings.api_key_hash_secret or settings.jwt_secret or "moldy-dev-agent-api-secret"


def _hash_secret_material(key_id: str, secret: str) -> str:
    material = f"{key_id}.{secret}".encode()
    return hmac.new(_signing_secret().encode("utf-8"), material, sha256).hexdigest()


def _token_urlsafe_no_underscore(num_bytes: int, length: int) -> str:
    return secrets.token_urlsafe(num_bytes).replace("-", "").replace("_", "")[:length]


def generate_api_key() -> GeneratedApiKey:
    key_id = "ak" + _token_urlsafe_no_underscore(12, 16)
    secret = _token_urlsafe_no_underscore(24, 32)
    cleartext = f"{KEY_PREFIX}_{key_id}_{secret}"
    return GeneratedApiKey(
        key_id=key_id,
        secret=secret,
        cleartext=cleartext,
        secret_hash=_hash_secret_material(key_id, secret),
        prefix=f"{KEY_PREFIX}_{key_id[:8]}",
        last_four=cleartext[-4:],
    )


def parse_api_key(raw: str) -> tuple[str, str] | None:
    parts = raw.strip().split("_", 3)
    if len(parts) != 4:
        return None
    if "_".join(parts[:2]) != KEY_PREFIX:
        return None
    key_id = parts[2]
    secret = parts[3]
    if not key_id.startswith("ak") or not secret:
        return None
    return key_id, secret


def verify_secret(key_id: str, secret: str, stored_hash: str) -> bool:
    expected = _hash_secret_material(key_id, secret)
    return hmac.compare_digest(expected, stored_hash)
