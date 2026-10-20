"""Company shared credentials (``scope='tenant'``) reaching a run's organization.

A company credential is usable by a user acting in an organization the company designated
it to (``tenants.settings.credential_orgs``). Legacy and shadow modes answer from that
designation; enforced mode asks OpenFGA ``credential#can_use`` for the *user* principal
(the designation fans out as ``organization#member`` consumers, and an agent principal is
never an organization member).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.company_catalog import company_tier
from app.authz.error_codes import authorization_error
from app.authz.principal import Principal, RunSource
from app.authz.run_decisions import require_decided
from app.authz.runtime import runtime_authz
from app.authz.service_types import Verdict
from app.exceptions import AppError
from app.models.credential import Credential
from app.models.organization import Tenant


def is_company_credential(credential: Credential, tenant_id: UUID) -> bool:
    return (
        credential.scope == "tenant"
        and credential.tenant_id == tenant_id
        and credential.org_id is None
        and credential.user_id is None
    )


async def designated_to(db: AsyncSession, credential: Credential, org_id: UUID) -> bool:
    tenant = await db.get(Tenant, credential.tenant_id)
    return tenant is not None and company_tier(tenant).credential_reaches(credential.id, org_id)


async def company_credential_usable(
    db: AsyncSession,
    credential: Credential,
    *,
    user_id: UUID,
    org_id: UUID,
    tenant_id: UUID,
    run_source: RunSource = "CHAT",
) -> bool:
    if not is_company_credential(credential, tenant_id) or credential.status != "active":
        return False
    reaches = await designated_to(db, credential, org_id)
    try:
        await require_decided(
            runtime_authz(),
            Principal("user", user_id, org_id, run_source=run_source),
            "can_use",
            f"credential:{credential.id}",
            min_visible="can_use",
            resource_org_id=org_id,
            legacy=Verdict.ALLOW if reaches else Verdict.NOT_FOUND,
            not_found=authorization_error("not_found"),
        )
    except AppError as denied:
        if denied.status in (403, 404):
            return False
        raise
    return True


async def company_credentials_for(
    db: AsyncSession, tenant_id: UUID, definition_key: str
) -> list[Credential]:
    """Active company credentials of one type, newest first (callers check the reach)."""
    rows = await db.scalars(
        select(Credential)
        .where(
            Credential.scope == "tenant",
            Credential.tenant_id == tenant_id,
            Credential.org_id.is_(None),
            Credential.definition_key == definition_key,
            Credential.status == "active",
        )
        .order_by(Credential.created_at.desc(), Credential.id)
    )
    return list(rows)
