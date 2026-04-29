"""Credential persistence + audit-log service.

Handles encrypt/decrypt round trips, audit log emission, and the small set of
common queries used by the router. Kept separate from
``app/services/credential_service.py`` (legacy) which is scheduled for deletion
in M5.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.external_secrets import resolve_external_refs
from app.models.credential import Credential
from app.models.credential_audit_log import CredentialAuditLog
from app.security import cipher
from app.security.key_provider import get_active_key, get_keys

logger = logging.getLogger(__name__)


# -- Encrypt / decrypt -------------------------------------------------------


def encrypt_data(data: dict[str, Any]) -> tuple[str, str, list[str]]:
    """Serialize and encrypt ``data``. Returns ``(blob, key_id, field_keys)``."""

    active = get_active_key()
    blob = cipher.encrypt(json.dumps(data, ensure_ascii=False), active)
    return blob, active.key_id, sorted(data.keys())


def decrypt_data(blob: str) -> dict[str, Any]:
    """Decrypt ``blob`` to a dict."""

    plaintext = cipher.decrypt(blob, get_keys())
    parsed = json.loads(plaintext)
    if not isinstance(parsed, dict):
        raise ValueError("decrypted credential payload is not an object")
    return parsed


async def decrypt_with_external(blob: str) -> dict[str, Any]:
    """Decrypt and resolve any ``__external__`` references in the payload."""

    raw = decrypt_data(blob)
    return await resolve_external_refs(raw)


# -- Queries -----------------------------------------------------------------


async def list_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> list[Credential]:
    result = await db.execute(
        select(Credential)
        .where(Credential.user_id == user_id)
        .order_by(Credential.created_at.desc())
    )
    return list(result.scalars().all())


async def get_for_user(
    db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID
) -> Credential | None:
    result = await db.execute(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


# -- Audit log ---------------------------------------------------------------


async def write_audit_log(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    source: str = "api",
    ip: str | None = None,
    user_agent: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CredentialAuditLog:
    log = CredentialAuditLog(
        credential_id=credential_id,
        actor_user_id=actor_user_id,
        action=action,
        source=source,
        ip=ip,
        user_agent=user_agent,
        error=error,
        log_metadata=metadata,
    )
    db.add(log)
    return log


# -- Mutations ---------------------------------------------------------------


async def create(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    definition_key: str,
    name: str,
    data: dict[str, Any],
    is_shared: bool = False,
    source: str = "api",
) -> Credential:
    blob, key_id, field_keys = encrypt_data(data)
    cred = Credential(
        user_id=user_id,
        definition_key=definition_key,
        name=name,
        data_encrypted=blob,
        key_id=key_id,
        field_keys=field_keys,
        is_shared=is_shared,
        status="active",
    )
    db.add(cred)
    await db.flush()
    await write_audit_log(
        db,
        credential_id=cred.id,
        actor_user_id=user_id,
        action="create",
        source=source,
        metadata={"definition_key": definition_key},
    )
    return cred


async def update(
    db: AsyncSession,
    *,
    credential: Credential,
    actor_user_id: uuid.UUID | None,
    name: str | None = None,
    data: dict[str, Any] | None = None,
    is_shared: bool | None = None,
    status: str | None = None,
    source: str = "api",
) -> Credential:
    metadata: dict[str, Any] = {}
    if name is not None and name != credential.name:
        credential.name = name
        metadata["name_changed"] = True
    if data is not None:
        blob, key_id, field_keys = encrypt_data(data)
        credential.data_encrypted = blob
        credential.key_id = key_id
        credential.field_keys = field_keys
        metadata["data_changed"] = True
    if is_shared is not None:
        credential.is_shared = is_shared
    if status is not None:
        credential.status = status
        metadata["status"] = status
    if metadata:
        await write_audit_log(
            db,
            credential_id=credential.id,
            actor_user_id=actor_user_id,
            action="update",
            source=source,
            metadata=metadata,
        )
    return credential


async def record_test(
    db: AsyncSession,
    *,
    credential: Credential,
    actor_user_id: uuid.UUID | None,
    result: dict[str, Any],
    source: str = "api",
) -> None:
    credential.last_tested_at = datetime.now(UTC).replace(tzinfo=None)
    credential.last_test_result = result
    await write_audit_log(
        db,
        credential_id=credential.id,
        actor_user_id=actor_user_id,
        action="test",
        source=source,
        metadata={"success": bool(result.get("success"))},
        error=None if result.get("success") else result.get("message"),
    )


async def re_encrypt_with_active_key(
    db: AsyncSession,
    credential: Credential,
    *,
    actor_user_id: uuid.UUID | None = None,
    source: str = "rotation",
) -> Credential:
    """Re-encrypt ``credential.data_encrypted`` with the active key.

    The blob is decrypted with whichever key it was originally written under
    (``cipher.decrypt`` walks all configured keys), then re-encrypted with the
    current active key. ``key_id`` is updated and a ``rotate`` audit log is
    written. Caller commits.
    """

    plaintext = decrypt_data(credential.data_encrypted)
    new_blob, new_key_id, _ = encrypt_data(plaintext)
    if new_key_id == credential.key_id and new_blob == credential.data_encrypted:
        return credential
    credential.data_encrypted = new_blob
    credential.key_id = new_key_id
    await write_audit_log(
        db,
        credential_id=credential.id,
        actor_user_id=actor_user_id,
        action="rotate",
        source=source,
        metadata={"key_id": new_key_id},
    )
    return credential


async def list_audit_logs(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID,
    limit: int = 50,
) -> list[CredentialAuditLog]:
    result = await db.execute(
        select(CredentialAuditLog)
        .where(CredentialAuditLog.credential_id == credential_id)
        .order_by(CredentialAuditLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


__all__ = [
    "create",
    "decrypt_data",
    "decrypt_with_external",
    "encrypt_data",
    "get_for_user",
    "list_audit_logs",
    "list_for_user",
    "re_encrypt_with_active_key",
    "record_test",
    "update",
    "write_audit_log",
]
