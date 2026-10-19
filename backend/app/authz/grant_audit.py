"""Audit rows of grant changes (plan 14.1 "공유"; split out of ``grants.py``)."""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.tuple_rows import GrantRow
from app.models.authz import ResourceGrant
from app.services import audit_service

AuditExtra = Mapping[str, object] | None  # caller facts merged into the audit metadata


def grant_terms(row: GrantRow | ResourceGrant) -> dict[str, object]:
    expires = row.expires_at.isoformat() if row.expires_at else None
    return {
        "expires_at": expires,
        "condition": row.condition_name,
        "context": row.condition_context,
    }


async def audit_grant(
    db: AsyncSession,
    actor: UUID | None,
    action: str,
    grant: ResourceGrant,
    before: GrantRow | None = None,
    *,
    extra: AuditExtra = None,
) -> None:
    metadata: dict[str, object] = {
        **(extra or {}),
        "grant_id": str(grant.id),
        "org_id": str(grant.org_id),
        "relation": grant.relation,
        "principal_type": grant.principal_type,
        "principal_id": str(grant.principal_id),
        "grant_kind": grant.grant_kind,
        **grant_terms(grant),
    }
    if before is not None:
        metadata["before"] = grant_terms(before)
    await audit_service.record_event(
        db,
        actor_type="user" if actor is not None else "system",
        actor_user_id=actor,
        action=action,
        target_type=grant.resource_type,
        target_id=grant.resource_id,
        outcome="success",
        metadata=metadata,
    )
