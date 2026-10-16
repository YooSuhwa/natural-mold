"""Credential payload sealing with format 0x01 (instance key) or 0x02 (company key).

Company-scoped credentials (``tenant_id`` set, not ``is_system``) are written with
the company data key when ``TENANT_DATA_KEYS_ENABLED`` is on; system/platform rows
always stay on the instance key. Reads accept both formats regardless of the flag.
Format 0x02 rows store ``key_id = "t:<company key id>"`` so the instance rotation
job can tell them apart without decrypting. Once a company's migration has closed
format 0x01 (``legacy_01_closed_at``), 0x01 ciphertext on its rows is rejected
while the flag is on; writing a 0x01 company row with the flag off reopens it.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_object_session

from app.config import settings
from app.models.credential import Credential
from app.security import cipher, tenant_keys
from app.security.cipher import TenantKeyError
from app.security.key_provider import get_active_key, get_keys

TENANT_KEY_PREFIX = "t:"
_TABLE = "credentials"
_FIELD = "data_encrypted"


def _company_row(credential: Credential) -> bool:
    return credential.tenant_id is not None and not credential.is_system


def uses_tenant_key(credential: Credential) -> bool:
    return bool(getattr(settings, "tenant_data_keys_enabled", False)) and _company_row(credential)


def _aad(credential: Credential) -> bytes:
    return cipher.tenant_aad(
        tenant_id=credential.tenant_id, table=_TABLE, row_id=credential.id, field=_FIELD
    )


def _session(credential: Credential, db: AsyncSession | None) -> AsyncSession:
    session = db or async_object_session(credential)
    if session is None:
        raise TenantKeyError("company-key credential must be attached to a session")
    return session


def _parse(plaintext: str) -> dict[str, Any]:
    parsed = json.loads(plaintext)
    if not isinstance(parsed, dict):
        raise ValueError("decrypted credential payload is not an object")
    return parsed


async def seal(
    credential: Credential,
    data: dict[str, Any],
    *,
    db: AsyncSession | None = None,
    key: tenant_keys.TenantDataKey | None = None,
) -> None:
    """Encrypt ``data`` into ``credential`` (blob, key id, field-key cache).

    ``key`` pins a specific company key (rotation); otherwise the active one is used.
    """

    plaintext = json.dumps(data, ensure_ascii=False)
    tenant_id = credential.tenant_id
    if uses_tenant_key(credential) and tenant_id is not None:
        if credential.id is None:
            raise TenantKeyError("company-key credential needs a row id before sealing")
        if key is None:
            key = await tenant_keys.ensure_active_key(_session(credential, db), tenant_id)
        blob = cipher.encrypt_tenant(
            plaintext, data_key=key.raw, key_id=key.key_id, aad=_aad(credential)
        )
        key_id = TENANT_KEY_PREFIX + key.key_id
    else:
        session = db or async_object_session(credential)
        if tenant_id is not None and not credential.is_system and session is not None:
            await tenant_keys.reopen_legacy_01(session, tenant_id)
        active = get_active_key()
        blob, key_id = cipher.encrypt(plaintext, active), active.key_id
    credential.data_encrypted = blob
    credential.key_id = key_id
    credential.field_keys = sorted(data.keys())


async def open_payload(credential: Credential, *, db: AsyncSession | None = None) -> dict[str, Any]:
    """Decrypt ``credential.data_encrypted`` in either format."""

    blob = credential.data_encrypted
    if cipher.blob_version(blob) != 2:
        if (
            uses_tenant_key(credential)
            and credential.tenant_id is not None
            and await tenant_keys.legacy_01_closed(_session(credential, db), credential.tenant_id)
        ):
            raise TenantKeyError("format 0x01 is closed for this company's credentials")
        return _parse(cipher.decrypt(blob, get_keys()))
    if credential.tenant_id is None:
        raise TenantKeyError("company-key ciphertext on a row without a company")
    key = await tenant_keys.get_key(
        _session(credential, db), credential.tenant_id, cipher.tenant_blob_key_id(blob)
    )
    return _parse(cipher.decrypt_tenant(blob, data_key=key.raw, aad=_aad(credential)))


def require_row_context(blob: str) -> None:
    """Reject a bare 0x02 blob: its AAD needs the owning row."""

    if cipher.blob_version(blob) == 2:
        raise TenantKeyError("company-key ciphertext needs its credential row to decrypt")
