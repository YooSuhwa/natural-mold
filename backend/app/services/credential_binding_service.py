"""Per-user connections to ``per_user`` tools and MCP servers (plan 9.x, 11.3, S23; F-04).

``CRUD /api/credential-bindings`` is ``SELF`` + target ``can_use`` + credential
``can_use``: the caller connects one of their own (or shared-to-them) credentials to a
tool or MCP server they may use. Runtime resolution of these rows is F-05.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.credential_binding_scope import binding_of, my_bindings
from app.authz.error_codes import authorization_error
from app.authz.org_context import OrgContext
from app.authz.resource_gate import require_same_org
from app.authz.service import AuthzService
from app.credentials.registry import registry as credential_registry
from app.credentials.validation import require_credential_use
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.resource_credential_binding import ResourceCredentialBinding as Binding
from app.models.tool import Tool
from app.services import audit_service, mcp_service, tool_service
from app.tools.registry import registry as tool_registry

Target = Tool | McpServer


async def load_target(
    db: AsyncSession,
    ctx: OrgContext,
    authz: AuthzService,
    resource_type: str,
    resource_id: uuid.UUID,
    permission: str = "can_use",
) -> Target:
    """The tool or MCP server for ``permission`` (hidden or foreign: its 404)."""
    if resource_type == "tool":
        return await tool_service.load_owned(
            db, resource_id, ctx, authz=authz, permission=permission
        )
    return await mcp_service.load_owned(db, resource_id, ctx, authz=authz, permission=permission)


def accepted_definition_keys(target: Target) -> frozenset[str]:
    """Types a ``per_user`` resource may require: the tool definition's, or (MCP) any
    registered credential definition."""
    if isinstance(target, Tool):
        definition = tool_registry.get(target.definition_key)
        return frozenset(definition.credential_definition_keys if definition else ())
    return frozenset(definition.key for definition in credential_registry.all())


def accepted_types(target: Target) -> frozenset[str]:
    """A connection must have the resource's recorded type (none recorded: nothing)."""
    key = target.credential_definition_key
    return frozenset({key}) if key and key in accepted_definition_keys(target) else frozenset()


async def list_mine(
    db: AsyncSession, ctx: OrgContext, resource_type: str | None = None
) -> list[Binding]:
    query = select(Binding).where(my_bindings(ctx))
    if resource_type is not None:
        query = query.where(Binding.resource_type == resource_type)
    rows = await db.scalars(query.order_by(Binding.created_at, Binding.id))
    return list(rows)


async def count_for(db: AsyncSession, resource_type: str, resource_id: uuid.UUID) -> int:
    query = select(func.count()).select_from(Binding).where(binding_of(resource_type, resource_id))
    return int(await db.scalar(query) or 0)


async def _mine(
    db: AsyncSession, ctx: OrgContext, resource_type: str, resource_id: uuid.UUID
) -> Binding | None:
    return await db.scalar(
        select(Binding).where(my_bindings(ctx), binding_of(resource_type, resource_id))
    )


async def _credential(
    db: AsyncSession, ctx: OrgContext, authz: AuthzService, target: Target, cid: uuid.UUID
) -> Credential:
    credential = await require_credential_use(db, credential_id=cid, context=ctx, authz=authz)
    if credential is None:  # unreachable: an id was given
        raise authorization_error("not_found")
    require_same_org(authz, credential, ctx.org_id)
    accepted = accepted_types(target)
    if credential.definition_key not in accepted:
        raise authorization_error("credential_type_mismatch", {"accepted": sorted(accepted)})
    return credential


async def bind(
    db: AsyncSession,
    ctx: OrgContext,
    authz: AuthzService,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    credential_id: uuid.UUID,
    request: Request | None = None,
) -> Binding:
    target = await load_target(db, ctx, authz, resource_type, resource_id)
    if target.credential_mode != "per_user":
        raise authorization_error("credential_mode_not_per_user")
    credential = await _credential(db, ctx, authz, target, credential_id)
    row = await _mine(db, ctx, resource_type, resource_id)
    previous = None if row is None else row.credential_id
    if row is None:
        row = Binding(
            org_id=ctx.org_id,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            credential_id=credential.id,
        )
        db.add(row)
    else:
        row.credential_id = credential.id
    await db.flush()
    await _audit(db, ctx, audit_actions.CREDENTIAL_BINDING_CREATE, row, request, previous)
    return row


async def unbind(
    db: AsyncSession,
    ctx: OrgContext,
    *,
    resource_type: str,
    resource_id: uuid.UUID,
    request: Request | None = None,
) -> None:
    """Only the caller's own row; no target check (access may already be gone)."""
    row = await _mine(db, ctx, resource_type, resource_id)
    if row is None:
        raise authorization_error("not_found")
    await _audit(db, ctx, audit_actions.CREDENTIAL_BINDING_DELETE, row, request, None)
    await db.delete(row)
    await db.flush()


async def _audit(
    db: AsyncSession,
    ctx: OrgContext,
    action: str,
    row: Binding,
    request: Request | None,
    replaced: uuid.UUID | None,
) -> None:
    metadata = {
        "org_id": str(ctx.org_id),
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id),
        "credential_id": str(row.credential_id),
    }
    if replaced is not None:
        metadata["replaced_credential_id"] = str(replaced)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=ctx.user_id,
        action=action,
        target_type=row.resource_type,
        target_id=row.resource_id,
        outcome="success",
        request=request,
        metadata=metadata,
        tenant_id=ctx.tenant_id,
    )


__all__ = [
    "accepted_definition_keys",
    "accepted_types",
    "bind",
    "count_for",
    "list_mine",
    "load_target",
    "unbind",
]
