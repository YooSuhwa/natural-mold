from __future__ import annotations

import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.error_codes import credential_not_found
from app.models.credential import Credential
from app.models.tool import MCPServer, Tool
from app.schemas.credential import CredentialCreate, CredentialUpdate
from app.services.encryption import decrypt_api_key, encrypt_api_key


async def list_credentials(
    db: AsyncSession, user_id: uuid.UUID
) -> list[Credential]:
    result = await db.execute(
        select(Credential)
        .where(Credential.user_id == user_id)
        .order_by(Credential.created_at.desc())
    )
    return list(result.scalars().all())


async def get_credential(
    db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID
) -> Credential:
    result = await db.execute(
        select(Credential).where(
            Credential.id == credential_id,
            Credential.user_id == user_id,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise credential_not_found()
    return cred


async def create_credential(
    db: AsyncSession, user_id: uuid.UUID, data: CredentialCreate
) -> Credential:
    encrypted = encrypt_api_key(json.dumps(data.data))
    cred = Credential(
        user_id=user_id,
        name=data.name,
        credential_type=data.credential_type,
        provider_name=data.provider_name,
        data_encrypted=encrypted,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


async def update_credential(
    db: AsyncSession,
    credential_id: uuid.UUID,
    user_id: uuid.UUID,
    data: CredentialUpdate,
) -> Credential:
    cred = await get_credential(db, credential_id, user_id)
    if data.name is not None:
        cred.name = data.name
    if data.data is not None:
        cred.data_encrypted = encrypt_api_key(json.dumps(data.data))
    await db.commit()
    await db.refresh(cred)
    return cred


async def delete_credential(
    db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    cred = await get_credential(db, credential_id, user_id)
    await db.delete(cred)
    await db.commit()


def resolve_credential_data(credential: Credential) -> dict[str, str]:
    """Decrypt credential data. Internal use only — never expose to API."""
    return json.loads(decrypt_api_key(credential.data_encrypted))


async def get_usage_count(
    db: AsyncSession, credential_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, int]:
    # Verify ownership
    await get_credential(db, credential_id, user_id)

    tool_count_result = await db.execute(
        select(func.count())
        .select_from(Tool)
        .where(Tool.credential_id == credential_id)
    )
    tool_count = tool_count_result.scalar() or 0

    mcp_count_result = await db.execute(
        select(func.count())
        .select_from(MCPServer)
        .where(MCPServer.credential_id == credential_id)
    )
    mcp_count = mcp_count_result.scalar() or 0

    return {"tool_count": tool_count, "mcp_server_count": mcp_count}
