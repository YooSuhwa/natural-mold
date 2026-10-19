"""Read model for an SSO connection (secrets reported only as present/absent)."""

from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.error_codes import authorization_error
from app.models.organization_identity import SsoConnection
from app.schemas.sso_connection import SsoConnectionRead
from app.services.sso.common import callback_url


def connection_view(request: Request, row: SsoConnection) -> SsoConnectionRead:
    oidc = row.protocol == "oidc"
    secret = row.client_secret_encrypted if oidc else row.ldap_bind_password_encrypted
    return SsoConnectionRead.model_validate(
        {
            "id": row.id,
            "protocol": row.protocol,
            "status": row.status,
            "issuer": row.issuer,
            "client_id": row.client_id,
            "has_secret": bool(secret),
            "scopes": list(row.scopes or []),
            "claim_mapping": dict(row.claim_mapping or {}),
            "server_url": row.ldap_server_url,
            "start_tls": row.ldap_start_tls,
            "bind_dn": row.ldap_bind_dn,
            "user_base_dn": row.ldap_user_base_dn,
            "user_filter": row.ldap_user_filter,
            "group_base_dn": row.ldap_group_base_dn,
            "group_filter": row.ldap_group_filter,
            "attribute_mapping": dict(row.ldap_attribute_mapping or {}),
            "has_ca_certificate": bool(row.ldap_ca_certificate),
            "group_role_mapping": dict(row.group_role_mapping or {}),
            "jit_enabled": row.jit_enabled,
            "trust_email_claim": row.trust_email_claim,
            "default_role": row.default_role,
            "tested_at": row.tested_at,
            "redirect_uri": callback_url(request, row.id) if oidc else None,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


async def owned_connection(
    db: AsyncSession, tenant_id: UUID, connection_id: UUID, *, lock: bool = False
) -> SsoConnection:
    query = select(SsoConnection).where(
        SsoConnection.id == connection_id, SsoConnection.tenant_id == tenant_id
    )
    if lock:
        query = query.with_for_update()
    row = await db.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise authorization_error("not_found")
    return row
