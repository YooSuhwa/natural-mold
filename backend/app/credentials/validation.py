from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.resource_gate import CREDENTIAL, gate_row
from app.authz.service import AuthzService
from app.credentials import service as credential_service
from app.credentials.company_use import company_credential_usable, is_company_credential
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
    allow_company: bool = False,
) -> Credential | None:
    """Attaching a credential needs ``credential#can_use`` (plan 9.5).

    The owner-only lookup above stays the legacy answer; without ``authz`` it is
    the whole check. ``allow_company``: a tool or MCP server's shared credential may also be
    a company shared credential the company designated to the caller's organization.
    """

    if allow_company and credential_id is not None:
        company = await db.get(Credential, credential_id)
        if company is not None and is_company_credential(company, context.tenant_id):
            if await company_credential_usable(
                db,
                company,
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
            ):
                return company
            raise HTTPException(status_code=404, detail="credential not found")
    if credential_id is None or authz is None:
        return await require_user_credential(db, credential_id=credential_id, context=context)

    async def legacy() -> Credential | None:
        return await require_user_credential(db, credential_id=credential_id, context=context)

    return await gate_row(authz, db, context, CREDENTIAL, credential_id, "can_use", legacy)


__all__ = ["require_credential_use", "require_user_credential"]
