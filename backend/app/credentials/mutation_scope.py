"""Required scope and typed inputs for credential mutation/read boundaries."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, assert_never
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.platform_scope import PlatformScope
from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.authz.scoping import private_scoped
from app.error_codes import credential_not_found
from app.models.credential import Credential


@dataclass(frozen=True, slots=True)
class CredentialPatch:
    credential_id: UUID
    actor_user_id: UUID
    name: str | None = None
    data: Mapping[str, JsonValue] | None = None
    is_shared: bool | None = None
    status: Literal["active", "disabled", "expired"] | None = None
    source: str = "api"


@dataclass(frozen=True, slots=True)
class CredentialTestRecord:
    credential_id: UUID
    actor_user_id: UUID
    result: Mapping[str, JsonValue]
    source: str = "api"


@dataclass(frozen=True, slots=True)
class CredentialAuditQuery:
    credential_id: UUID
    limit: int = 50


async def load_scoped_credential(
    db: AsyncSession, credential_id: UUID, context: OrgContext | PlatformScope
) -> Credential:
    query = select(Credential).where(Credential.id == credential_id).with_for_update()
    match context:
        case OrgContext():
            await resolve_private_actor(
                db, PrivateResourceActor(context.user_id, context.org_id, context.tenant_id)
            )
            query = private_scoped(query, Credential, context).where(
                Credential.is_system.is_(False)
            )
        case PlatformScope():
            query = query.where(
                Credential.is_system.is_(True),
                Credential.user_id.is_(None),
                Credential.org_id.is_(None),
                Credential.tenant_id.is_(None),
                Credential.scope == "platform",
            )
        case unreachable:
            assert_never(unreachable)
    credential = await db.scalar(query.execution_options(populate_existing=True))
    if credential is None:
        raise credential_not_found()
    return credential
