from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.resource_gate import CREDENTIAL, gate_row
from app.authz.service import AuthzService
from app.credentials import service as credential_service
from app.models.credential import Credential


async def require_user_credential(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID | None,
    context: OrgContext,
) -> Credential | None:
    """Return a user-owned, non-system credential or raise a neutral 404."""

    if credential_id is None:
        return None
    credential = await credential_service.get_for_user(db, credential_id, context)
    if credential is None:
        raise HTTPException(status_code=404, detail="credential not found")
    return credential


async def require_credential_use(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID | None,
    context: OrgContext,
    authz: AuthzService | None,
) -> Credential | None:
    """Attaching a credential needs ``credential#can_use`` (plan 9.5).

    The owner-only lookup above stays the legacy answer; without ``authz`` it is
    the whole check.
    """

    if credential_id is None or authz is None:
        return await require_user_credential(db, credential_id=credential_id, context=context)

    async def legacy() -> Credential | None:
        return await require_user_credential(db, credential_id=credential_id, context=context)

    return await gate_row(authz, db, context, CREDENTIAL, credential_id, "can_use", legacy)


__all__ = ["require_credential_use", "require_user_credential"]
