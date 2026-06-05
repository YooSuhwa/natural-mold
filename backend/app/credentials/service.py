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
from app.services import audit_service

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


async def list_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[Credential]:
    """Return user-facing credentials only — system rows are hidden.

    Pickers (model Health, agent settings, MCP wizard) call this so a
    misclick doesn't bind an operator key to a user agent.
    """

    result = await db.execute(
        select(Credential)
        .where(
            Credential.user_id == user_id,
            Credential.is_system.is_(False),
        )
        .order_by(Credential.created_at.desc())
    )
    return list(result.scalars().all())


async def get_for_user(
    db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID
) -> Credential | None:
    """Look up a user-facing credential. System rows return None here so
    user-facing endpoints can't accidentally read them."""

    result = await db.execute(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.user_id == user_id,
            Credential.is_system.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def list_system(db: AsyncSession) -> list[Credential]:
    """Return operator-managed system credentials regardless of owner."""

    result = await db.execute(
        select(Credential)
        .where(Credential.is_system.is_(True))
        .order_by(Credential.created_at.desc())
    )
    return list(result.scalars().all())


async def get_system(db: AsyncSession, credential_id: uuid.UUID) -> Credential | None:
    """Look up a system credential by id (no user filter)."""

    result = await db.execute(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.is_system.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def find_system_by_definition(db: AsyncSession, definition_key: str) -> Credential | None:
    """First active system credential matching ``definition_key`` (e.g.
    ``anthropic``). Powers the assistant agent's tiered ENV → system
    credential fallback."""

    result = await db.execute(
        select(Credential)
        .where(
            Credential.is_system.is_(True),
            Credential.definition_key == definition_key,
            Credential.status == "active",
        )
        .order_by(Credential.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# -- LLM provider key bulk reader --------------------------------------------

# ADR-013: maps Credential.definition_key → ``_ENV_FALLBACK`` key (the latter
# matches ``settings.<key>_api_key``). ``openai_compatible`` is intentionally
# omitted — builder/assistant sub-agents need a single api_key plus base_url
# triple, which the env-fallback dict can't represent. Those flows still go
# through ``Agent.llm_credential`` (chat_service path).
LLM_DEFINITION_TO_ENV_KEY: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google_genai": "google",
    "openrouter": "openrouter",
}

# Used by the chat-time credential resolver (``resolve_llm_api_key_for_agent``)
# to find a user's credential by ``Model.provider``. Includes
# ``openai_compatible`` even though it's absent from
# ``LLM_DEFINITION_TO_ENV_KEY``: the env-fallback exclusion is about not being
# representable as a single ENV string, but the chat resolver merely needs a
# provider→definition_key lookup and doesn't share that constraint.
PROVIDER_TO_DEFINITION_KEY: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google_genai",
    "openrouter": "openrouter",
    "openai_compatible": "openai_compatible",
}


def is_llm_definition(definition_key: str) -> bool:
    """True when ``definition_key`` participates in ``_ENV_FALLBACK`` sync."""

    return definition_key in LLM_DEFINITION_TO_ENV_KEY


async def get_provider_keys(
    db: AsyncSession, *, system_only: bool = False
) -> dict[str, str | None]:
    """LLM provider → api_key dict for ``_ENV_FALLBACK`` sync (ADR-013).

    Returns a dict keyed by ``_ENV_FALLBACK`` key (``anthropic``/``openai``/
    ``google``/``openrouter``). Decrypts each matching credential and extracts
    the ``api_key`` (or ``token``) field.

    - When ``system_only=True`` only ``is_system=True`` rows are read.
    - When ``system_only=False`` system rows take precedence; user rows fill
      gaps. Within the same priority tier the most recently created row wins
      (``created_at DESC``). Rows that fail to decrypt are skipped with a log.

    Empty / missing entries are omitted from the result so the caller can
    decide its own fallback policy (typically: don't overwrite existing ENV).
    """

    stmt = (
        select(Credential)
        .where(
            Credential.definition_key.in_(LLM_DEFINITION_TO_ENV_KEY.keys()),
            Credential.status == "active",
        )
        .order_by(Credential.is_system.desc(), Credential.created_at.desc())
    )
    if system_only:
        stmt = stmt.where(Credential.is_system.is_(True))

    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    out: dict[str, str | None] = {}
    for cred in rows:
        env_key = LLM_DEFINITION_TO_ENV_KEY.get(cred.definition_key)
        if env_key is None or env_key in out:
            # ``is_system DESC`` puts system rows first, so the first hit per
            # env_key already represents the highest-priority credential.
            continue
        try:
            payload = await decrypt_with_external(cred.data_encrypted)
        except Exception:  # noqa: BLE001 — bad cipher / missing key shouldn't crash startup
            logger.exception(
                "get_provider_keys: failed to decrypt credential %s (%s)",
                cred.id,
                cred.definition_key,
            )
            continue
        api_key = payload.get("api_key") or payload.get("token")
        if api_key:
            out[env_key] = str(api_key)
    return out


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
    await audit_service.record_event(
        db,
        actor_type="user" if actor_user_id is not None else source,
        actor_user_id=actor_user_id,
        owner_user_id=actor_user_id,
        action=f"credential.{action}",
        target_type="credential",
        target_id=credential_id,
        outcome="failure" if error else "success",
        reason_code=action if error else None,
        reason_message=error,
        ip_address=ip,
        user_agent_value=user_agent,
        metadata={"source": source, **(metadata or {})},
    )
    return log


# -- Mutations ---------------------------------------------------------------


async def create(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    definition_key: str,
    name: str,
    data: dict[str, Any],
    is_shared: bool = False,
    is_system: bool = False,
    source: str = "api",
) -> Credential:
    """Create a credential row.

    ``is_system=True`` rows MUST be created with ``user_id=None`` — system
    credentials belong to the operator, not to any single user, so the FK is
    intentionally NULL (the m36 migration relaxed the constraint and added a
    CHECK enforcing this invariant). User-owned credentials must always
    supply a non-null ``user_id``.
    """

    if is_system and user_id is not None:
        raise ValueError("system credentials must have user_id=None")
    if not is_system and user_id is None:
        raise ValueError("user credentials require user_id")

    blob, key_id, field_keys = encrypt_data(data)
    cred = Credential(
        user_id=user_id,
        definition_key=definition_key,
        name=name,
        data_encrypted=blob,
        key_id=key_id,
        field_keys=field_keys,
        is_shared=is_shared,
        is_system=is_system,
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
    "LLM_DEFINITION_TO_ENV_KEY",
    "PROVIDER_TO_DEFINITION_KEY",
    "create",
    "decrypt_data",
    "decrypt_with_external",
    "encrypt_data",
    "find_system_by_definition",
    "get_for_user",
    "get_provider_keys",
    "get_system",
    "is_llm_definition",
    "list_audit_logs",
    "list_for_user",
    "list_system",
    "re_encrypt_with_active_key",
    "record_test",
    "update",
    "write_audit_log",
]
