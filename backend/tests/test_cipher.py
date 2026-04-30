"""Tests for Cipher V2 (AES-256-GCM with HKDF-SHA256 key derivation)."""

from __future__ import annotations

import base64
import secrets

import pytest

from app.security import cipher as cipher_mod
from app.security.cipher import (
    AES_KEY_LEN,
    AUTH_TAG_LEN,
    FORMAT_VERSION,
    HKDF_INFO,
    SALT_LEN,
    CipherKey,
    DecryptionError,
    InvalidKeyError,
    decrypt,
    encrypt,
    identify_active_key_id,
)


def _gen_key_hex() -> str:
    return secrets.token_hex(AES_KEY_LEN)


def _make_key() -> CipherKey:
    return CipherKey.from_hex(_gen_key_hex())


# ---------------------------------------------------------------------------
# Algorithm-level invariants
# ---------------------------------------------------------------------------


def test_hkdf_info_is_branded() -> None:
    """The HKDF info must be the Moldy-specific identifier."""

    assert HKDF_INFO == b"moldy-encryption-v1"


def test_format_version_is_v1() -> None:
    assert FORMAT_VERSION == b"\x01"


# ---------------------------------------------------------------------------
# CipherKey
# ---------------------------------------------------------------------------


def test_key_id_is_deterministic() -> None:
    hex_str = _gen_key_hex()
    a = CipherKey.from_hex(hex_str)
    b = CipherKey.from_hex(hex_str)
    assert a.key_id == b.key_id
    assert len(a.key_id) == 8


def test_key_id_differs_per_key() -> None:
    a = _make_key()
    b = _make_key()
    assert a.key_id != b.key_id


def test_invalid_key_hex_rejected() -> None:
    with pytest.raises(InvalidKeyError):
        CipherKey.from_hex("not-hex!!")


def test_short_key_rejected() -> None:
    with pytest.raises(InvalidKeyError):
        CipherKey.from_hex("aabb")  # only 2 bytes


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_simple_string() -> None:
    key = _make_key()
    ct = encrypt("hello world", key)
    assert decrypt(ct, [key]) == "hello world"


def test_round_trip_unicode() -> None:
    key = _make_key()
    plaintext = "안녕하세요 — 한국어 + emoji 🎉"
    ct = encrypt(plaintext, key)
    assert decrypt(ct, [key]) == plaintext


def test_round_trip_empty_string() -> None:
    key = _make_key()
    ct = encrypt("", key)
    assert decrypt(ct, [key]) == ""


def test_two_encryptions_produce_different_ciphertexts() -> None:
    """Random salt → semantically secure (no IV reuse)."""

    key = _make_key()
    a = encrypt("same plaintext", key)
    b = encrypt("same plaintext", key)
    assert a != b
    assert decrypt(a, [key]) == decrypt(b, [key]) == "same plaintext"


# ---------------------------------------------------------------------------
# Multi-key (rotation)
# ---------------------------------------------------------------------------


def test_multi_key_decrypts_with_old_key_when_first() -> None:
    old = _make_key()
    new = _make_key()
    ct = encrypt("legacy data", old)
    # During rotation, candidate list is [active_new, old, ...]; old must still
    # decrypt because its IV was derived from its own key material.
    assert decrypt(ct, [new, old]) == "legacy data"


def test_multi_key_first_match_wins() -> None:
    a = _make_key()
    b = _make_key()
    ct = encrypt("foo", a)
    # 'a' is in the candidates → succeeds regardless of order
    assert decrypt(ct, [a, b]) == "foo"
    assert decrypt(ct, [b, a]) == "foo"


def test_decrypt_fails_when_no_key_matches() -> None:
    real = _make_key()
    other1 = _make_key()
    other2 = _make_key()
    ct = encrypt("secret", real)
    with pytest.raises(DecryptionError) as excinfo:
        decrypt(ct, [other1, other2])
    msg = str(excinfo.value).lower()
    assert "could not decrypt" in msg or "candidate" in msg


def test_decrypt_with_empty_candidate_list_fails() -> None:
    key = _make_key()
    ct = encrypt("x", key)
    with pytest.raises(DecryptionError):
        decrypt(ct, [])


# ---------------------------------------------------------------------------
# Tampering / corruption detection
# ---------------------------------------------------------------------------


def test_truncated_blob_rejected() -> None:
    key = _make_key()
    ct = encrypt("secret", key)
    raw = base64.b64decode(ct)
    # Truncate to less than minimum
    truncated = base64.b64encode(raw[:10]).decode()
    with pytest.raises(DecryptionError):
        decrypt(truncated, [key])


def test_invalid_base64_rejected() -> None:
    key = _make_key()
    with pytest.raises(DecryptionError):
        decrypt("!!!not-base64!!!", [key])


def test_unsupported_version_rejected() -> None:
    key = _make_key()
    # Build a blob with version=0x02
    bogus = b"\x02" + b"\x00" * SALT_LEN + b"\x00" * AUTH_TAG_LEN + b"x"
    encoded = base64.b64encode(bogus).decode()
    with pytest.raises(DecryptionError) as excinfo:
        decrypt(encoded, [key])
    assert "version" in str(excinfo.value).lower()


def test_tampered_ciphertext_rejected() -> None:
    key = _make_key()
    ct = encrypt("secret", key)
    raw = bytearray(base64.b64decode(ct))
    # Flip a byte in the ciphertext region (after version + salt + tag)
    raw[-1] ^= 0xFF
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(DecryptionError):
        decrypt(tampered, [key])


def test_tampered_auth_tag_rejected() -> None:
    key = _make_key()
    ct = encrypt("secret", key)
    raw = bytearray(base64.b64decode(ct))
    # Flip a byte in the auth tag region
    tag_start = 1 + SALT_LEN
    raw[tag_start] ^= 0xFF
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(DecryptionError):
        decrypt(tampered, [key])


# ---------------------------------------------------------------------------
# Active key identification
# ---------------------------------------------------------------------------


def test_identify_active_key_id_returns_first() -> None:
    a = _make_key()
    b = _make_key()
    assert identify_active_key_id([a, b]) == a.key_id
    assert identify_active_key_id([b, a]) == b.key_id


def test_identify_active_key_id_empty_raises() -> None:
    with pytest.raises(InvalidKeyError):
        identify_active_key_id([])


# ---------------------------------------------------------------------------
# Blob structure sanity
# ---------------------------------------------------------------------------


def test_blob_structure() -> None:
    """The blob layout is [version 1B][salt 32B][tag 16B][ct]."""

    key = _make_key()
    ct = encrypt("abc", key)
    raw = base64.b64decode(ct)
    assert raw[0:1] == FORMAT_VERSION
    # GCM ciphertext length equals plaintext length; the tag is stored separately.
    assert len(raw) == 1 + SALT_LEN + AUTH_TAG_LEN + len("abc")


def test_module_exposes_aes_key_length_constant() -> None:
    """Sanity — Module-level constants must match the algorithm specification."""

    assert AES_KEY_LEN == 32
    assert SALT_LEN == 32
    assert AUTH_TAG_LEN == 16
    assert cipher_mod.IV_LEN == 12
