"""Validate persisted MCP probe ownership before resolving authentication."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.private_actor import PrivateResourceActor, resolve_private_actor
from app.credentials.company_use import designated_to, is_company_credential
from app.error_codes import credential_not_found, mcp_server_not_found
from app.models.credential import Credential
from app.models.mcp_server import McpServer


async def require_mcp_probe_scope(
    db: AsyncSession, server: McpServer, credential_id: UUID | None
) -> Credential | None:
    """Restore the server's actor; never select scope from browser preferences."""
    platform = server.is_system and server.org_id is None and server.tenant_id is None
    if not platform:
        if server.is_system or server.org_id is None or server.tenant_id is None:
            raise mcp_server_not_found()
        await resolve_private_actor(
            db, PrivateResourceActor(server.user_id, server.org_id, server.tenant_id)
        )
    if credential_id is None:
        return None
    credential = await db.scalar(
        select(Credential)
        .where(Credential.id == credential_id)
        .with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )
    if credential is None:
        raise credential_not_found()
    if platform:
        permitted = credential.is_system and (
            credential.user_id is None
            and credential.org_id is None
            and credential.tenant_id is None
            and credential.scope == "platform"
        )
    elif server.tenant_id is not None and is_company_credential(credential, server.tenant_id):
        # A company shared credential, while the company designates it to the server's org.
        permitted = server.org_id is not None and await designated_to(db, credential, server.org_id)
    else:
        permitted = not credential.is_system and (
            credential.user_id,
            credential.org_id,
            credential.tenant_id,
        ) == (server.user_id, server.org_id, server.tenant_id)
    if not permitted:
        raise credential_not_found()
    return credential
