"""Cipher V2 — AES-256-GCM with HKDF-SHA256 key derivation.

- HKDF-SHA256 derives both the AES key and IV from a per-message random salt
  combined with the instance key.
- AES-256-GCM provides authenticated encryption.
- Output is a single Base64-encoded blob: ``[version 1B][salt 32B][authTag 16B][ciphertext]``.
- The HKDF info string ``moldy-encryption-v1`` and version byte ``0x01`` identify
  this scheme.
- Company-scoped secrets may instead use format ``0x02`` (per-company data key,
  see the section at the end of this module and ``app.security.tenant_keys``).

Multi-key handling (rotation):
- The active key encrypts new data; all configured keys are tried in order
  during decryption (first success wins).
- Each key has a deterministic 8-character ``key_id`` (sha256 prefix) stored
  alongside the ciphertext so a row can be re-encrypted to the active key by
  the rotation cron without touching unrelated rows.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher as _AesCipher
from cryptography.hazmat.primitives.ciphers import algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# -- Constants (do not change without bumping FORMAT_VERSION) ----------------

FORMAT_VERSION = b"\x01"
HKDF_INFO = b"moldy-encryption-v1"
SALT_LEN = 32
IV_LEN = 12
AUTH_TAG_LEN = 16
HKDF_OUT_LEN = SALT_LEN + IV_LEN  # derived material: 32-byte AES key + 12-byte IV — wait, see below
# Note: HKDF derives AES_KEY_LEN (32) + IV_LEN (12) = 44 bytes total.
AES_KEY_LEN = 32
HKDF_TOTAL_LEN = AES_KEY_LEN + IV_LEN  # 44

# Minimum decoded blob length: version(1) + salt(32) + tag(16). GCM permits a zero-length
# ciphertext (auth tag still authenticates the empty message), so the minimum is 49 bytes.
_MIN_BLOB_LEN = 1 + SALT_LEN + AUTH_TAG_LEN


# -- Errors ------------------------------------------------------------------


class CipherError(Exception):
    """Base class for cipher errors."""


class InvalidKeyError(CipherError):
    """Raised when an encryption key is malformed."""


class DecryptionError(CipherError):
    """Raised when decryption fails (bad key, corrupted blob, wrong version)."""


class TenantKeyError(DecryptionError):
    """A company data key is unavailable, misconfigured or used without row context."""


# -- Key utilities -----------------------------------------------------------


@dataclass(frozen=True)
class CipherKey:
    """A single encryption key with a deterministic short identifier."""

    raw: bytes  # 32-byte AES key material
    key_id: str  # sha256(raw)[:8].hex()

    @classmethod
    def from_hex(cls, hex_str: str) -> CipherKey:
        try:
            raw = bytes.fromhex(hex_str.strip())
        except ValueError as exc:
            raise InvalidKeyError(f"key is not valid hex: {exc}") from exc
        if len(raw) != AES_KEY_LEN:
            raise InvalidKeyError(
                f"key must be {AES_KEY_LEN} bytes ({AES_KEY_LEN * 2} hex chars), "
                f"got {len(raw)} bytes"
            )
        return cls(raw=raw, key_id=hashlib.sha256(raw).hexdigest()[:8])


# -- Core encrypt / decrypt --------------------------------------------------


def _derive(key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive the AES key and IV from the instance key + per-message salt."""

    material = HKDF(
        algorithm=hashes.SHA256(),
        length=HKDF_TOTAL_LEN,
        salt=salt,
        info=HKDF_INFO,
    ).derive(key)
    return material[:AES_KEY_LEN], material[AES_KEY_LEN:]


def encrypt(plaintext: str, key: CipherKey) -> str:
    """Encrypt ``plaintext`` (UTF-8 string) and return a Base64 blob."""

    salt = os.urandom(SALT_LEN)
    aes_key, iv = _derive(key.raw, salt)

    encryptor = _AesCipher(algorithms.AES(aes_key), modes.GCM(iv)).encryptor()
    ciphertext = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
    auth_tag = encryptor.tag

    blob = FORMAT_VERSION + salt + auth_tag + ciphertext
    return base64.b64encode(blob).decode("ascii")


def decrypt(blob_b64: str, candidate_keys: list[CipherKey]) -> str:
    """Decrypt a Base64 blob, trying each candidate key in order.

    Raises ``DecryptionError`` if no key succeeds, the blob is malformed, or
    the format version is unsupported.
    """

    if not candidate_keys:
        raise DecryptionError("no candidate keys provided")

    try:
        blob = base64.b64decode(blob_b64.encode("ascii"), validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise DecryptionError(f"blob is not valid base64: {exc}") from exc

    if len(blob) < _MIN_BLOB_LEN:
        raise DecryptionError(f"blob too short ({len(blob)} bytes, need at least {_MIN_BLOB_LEN})")

    version = blob[0:1]
    if version != FORMAT_VERSION:
        raise DecryptionError(
            f"unsupported format version 0x{version.hex()}; expected 0x{FORMAT_VERSION.hex()}"
        )

    salt = blob[1 : 1 + SALT_LEN]
    auth_tag = blob[1 + SALT_LEN : 1 + SALT_LEN + AUTH_TAG_LEN]
    ciphertext = blob[1 + SALT_LEN + AUTH_TAG_LEN :]

    last_error: Exception | None = None
    for key in candidate_keys:
        try:
            aes_key, iv = _derive(key.raw, salt)
            decryptor = _AesCipher(algorithms.AES(aes_key), modes.GCM(iv, auth_tag)).decryptor()
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            return plaintext.decode("utf-8")
        except InvalidTag as exc:
            last_error = exc
            continue
        except Exception as exc:  # pragma: no cover — defensive
            last_error = exc
            continue

    raise DecryptionError(
        f"none of the {len(candidate_keys)} candidate key(s) could decrypt the blob"
    ) from last_error


def identify_active_key_id(keys: list[CipherKey]) -> str:
    """Return the active (first) key's ``key_id`` for storage alongside ciphertext."""

    if not keys:
        raise InvalidKeyError("no keys configured")
    return keys[0].key_id


# -- Format 0x02: company data key (envelope encryption, B-16) ---------------
#
# ``[0x02][company key id 6B][nonce 12B][authTag 16B][ciphertext]``. The AES-GCM
# AAD is the header plus ``tenant_aad(...)`` so a blob cannot be moved to another
# company, table, row or field. The data key itself is wrapped by the instance key
# (``key_provider.wrap_data_key``) and stored in ``tenants.data_keys``.

TENANT_FORMAT_VERSION = b"\x02"
TENANT_KEY_ID_LEN = 6
_TENANT_HEADER_LEN = 1 + TENANT_KEY_ID_LEN
_TENANT_MIN_LEN = _TENANT_HEADER_LEN + IV_LEN + AUTH_TAG_LEN


def _b64decode(blob_b64: str) -> bytes:
    try:
        return base64.b64decode(blob_b64.encode("ascii"), validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise DecryptionError("blob is not valid base64") from exc


def blob_version(blob_b64: str) -> int:
    """Return the format version byte of a stored blob (1 = instance key, 2 = company key)."""

    blob = _b64decode(blob_b64)
    if not blob:
        raise DecryptionError("blob is empty")
    return blob[0]


def tenant_aad(*, tenant_id: object, table: str, row_id: object, field: str) -> bytes:
    """Associated data that pins a company ciphertext to one company/table/row/field."""

    return "|".join(("moldy-tenant-v1", str(tenant_id), table, str(row_id), field)).encode()


def encrypt_tenant(plaintext: str, *, data_key: bytes, key_id: str, aad: bytes) -> str:
    """Encrypt with a company data key into format 0x02."""

    key_id_raw = bytes.fromhex(key_id)
    if len(key_id_raw) != TENANT_KEY_ID_LEN or len(data_key) != AES_KEY_LEN:
        raise InvalidKeyError("company data key or key id has an invalid length")
    header = TENANT_FORMAT_VERSION + key_id_raw
    nonce = os.urandom(IV_LEN)
    sealed = AESGCM(data_key).encrypt(nonce, plaintext.encode("utf-8"), header + aad)
    ciphertext, auth_tag = sealed[:-AUTH_TAG_LEN], sealed[-AUTH_TAG_LEN:]
    return base64.b64encode(header + nonce + auth_tag + ciphertext).decode("ascii")


def _tenant_blob(blob_b64: str) -> bytes:
    blob = _b64decode(blob_b64)
    if len(blob) < _TENANT_MIN_LEN or blob[0:1] != TENANT_FORMAT_VERSION:
        raise DecryptionError("blob is not a company-key (0x02) ciphertext")
    return blob


def tenant_blob_key_id(blob_b64: str) -> str:
    """Return the company key id (hex) embedded in a format 0x02 blob."""

    return _tenant_blob(blob_b64)[1:_TENANT_HEADER_LEN].hex()


def decrypt_tenant(blob_b64: str, *, data_key: bytes, aad: bytes) -> str:
    """Decrypt a format 0x02 blob; wrong key or AAD raises ``DecryptionError``."""

    blob = _tenant_blob(blob_b64)
    header = blob[:_TENANT_HEADER_LEN]
    nonce = blob[_TENANT_HEADER_LEN : _TENANT_HEADER_LEN + IV_LEN]
    auth_tag = blob[_TENANT_HEADER_LEN + IV_LEN : _TENANT_MIN_LEN]
    ciphertext = blob[_TENANT_MIN_LEN:]
    try:
        plaintext = AESGCM(data_key).decrypt(nonce, ciphertext + auth_tag, header + aad)
    except (InvalidTag, ValueError) as exc:
        raise DecryptionError("company data key could not decrypt the blob") from exc
    return plaintext.decode("utf-8")
