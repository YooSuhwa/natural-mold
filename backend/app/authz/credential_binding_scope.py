"""Ownership and cleanup of per-user connections (``resource_credential_bindings``; F-04).

A connection belongs to one caller in one organization (``SELF``): only its user reads
or removes it. Deleting the tool, MCP server or credential removes its connections
explicitly (``on_resource_deleted``), since the resource reference is polymorphic and
the credential FK is RESTRICT.
"""

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.org_context import OrgContext
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.resource_credential_binding import ResourceCredentialBinding as Binding
from app.models.tool import Tool


def my_bindings(ctx: OrgContext) -> ColumnElement[bool]:
    return (
        (Binding.user_id == ctx.user_id)
        & (Binding.org_id == ctx.org_id)
        & (Binding.tenant_id == ctx.tenant_id)
    )


def account_bindings(ctx: OrgContext, user_id: UUID) -> ColumnElement[bool]:
    """One run account's connections in the run's organization (F-05: the caller for
    caller-identity runs, the agent owner for fixed runs)."""
    return (
        (Binding.user_id == user_id)
        & (Binding.org_id == ctx.org_id)
        & (Binding.tenant_id == ctx.tenant_id)
    )


def binding_of(resource_type: str, resource_id: UUID) -> ColumnElement[bool]:
    return (Binding.resource_type == resource_type) & (Binding.resource_id == resource_id)


async def drop_bindings_for(db: AsyncSession, row: object) -> None:
    """Every caller's connection to a deleted tool or MCP server, or through a credential."""
    match row:
        case Tool():
            clause = binding_of("tool", row.id)
        case McpServer():
            clause = binding_of("mcp_server", row.id)
        case Credential():
            clause = Binding.credential_id == row.id
        case _:
            return
    await db.execute(delete(Binding).where(clause).execution_options(synchronize_session=False))


__all__ = ["account_bindings", "binding_of", "drop_bindings_for", "my_bindings"]
