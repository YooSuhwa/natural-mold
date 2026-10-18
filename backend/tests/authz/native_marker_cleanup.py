"""Native-lane cleanup for the revocation markers membership events record (plan 8.6).

Exits, role and group-membership changes record ``authz_revocations`` rows whose
``tenant_id`` restricts company deletion, so fixtures remove them before the company.
"""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import AuthzRevocation


async def delete_revocation_markers(db: AsyncSession, tenant_ids: Iterable[UUID]) -> None:
    ids = list(tenant_ids)
    if ids:
        await db.execute(delete(AuthzRevocation).where(AuthzRevocation.tenant_id.in_(ids)))
