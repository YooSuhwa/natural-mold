"""``/api/tenants/{tenant_id}/sso-connections`` CRUD and activation (plan 9.7, S30).

New connections start as ``draft``; only a passed test (``tested``) can be activated.
Changing anything the login depends on (issuer, client, secret, directory settings)
sends a tested/active connection back to ``draft`` ("활성 연결의 설정을 바꾸면 다시
시험"). The last active connection cannot be removed while SSO is required.
"""

from typing import Final
from uuid import UUID, uuid4

from fastapi import Request
from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.errors import sso_error
from app.auth.sso.ldap import require_encrypted_transport
from app.auth.sso.membership import parse_target
from app.auth.sso.policy import sso_policy
from app.auth.sso.secret_box import seal_secret
from app.authz import audit_actions
from app.models.organization import Organization, Tenant
from app.models.organization_group import Group
from app.models.organization_identity import SsoConnection
from app.schemas.sso_connection import (
    LdapConnectionCreate,
    OidcConnectionCreate,
    SsoConnectionList,
    SsoConnectionRead,
    SsoConnectionUpdate,
)
from app.services.sso.common import SsoMutation, audit, locked_admin
from app.services.sso.connection_view import connection_view, owned_connection
from app.tenancy.admin_context import TenantAdminContext

OIDC_FIELDS: Final = {"issuer": "issuer", "client_id": "client_id", "scopes": "scopes"}
LDAP_FIELDS: Final = {
    "server_url": "ldap_server_url",
    "start_tls": "ldap_start_tls",
    "bind_dn": "ldap_bind_dn",
    "user_base_dn": "ldap_user_base_dn",
    "user_filter": "ldap_user_filter",
    "group_base_dn": "ldap_group_base_dn",
    "group_filter": "ldap_group_filter",
    "attribute_mapping": "ldap_attribute_mapping",
    "ca_certificate": "ldap_ca_certificate",
}
OWN_FIELDS: Final = {
    "oidc": {**OIDC_FIELDS, "claim_mapping": "claim_mapping"},  # claims: no re-test
    "ldap": LDAP_FIELDS,
}
SECRET_KEYS: Final = {"oidc": "client_secret", "ldap": "bind_password"}
COMMON_FIELDS: Final = frozenset(
    {"jit_enabled", "default_role", "group_role_mapping", "disabled", "trust_email_claim"}
)
NULLABLE: Final = frozenset({"group_base_dn", "group_filter", "ca_certificate"})


async def validate_mapping(
    db: AsyncSession, tenant_id: UUID, mapping: dict[str, JsonValue]
) -> None:
    for entry in mapping.values():
        for raw in entry if isinstance(entry, list) else [entry]:
            target = parse_target(raw)
            org = await db.get(Organization, target.org_id) if target else None
            if target is None or org is None or org.tenant_id != tenant_id:
                raise sso_error("sso_config_invalid")
            if target.group_id is not None:
                group = await db.get(Group, target.group_id)
                if group is None or group.org_id != org.id:
                    raise sso_error("sso_config_invalid")


async def list_connections(
    db: AsyncSession, request: Request, tenant_id: UUID
) -> SsoConnectionList:
    rows = await db.scalars(
        select(SsoConnection)
        .where(SsoConnection.tenant_id == tenant_id)
        .order_by(SsoConnection.created_at, SsoConnection.id)
    )
    return SsoConnectionList(items=[connection_view(request, row) for row in rows])


def _new_row(tenant: Tenant, payload: OidcConnectionCreate | LdapConnectionCreate) -> SsoConnection:
    common = {
        "id": uuid4(),
        "tenant_id": tenant.id,
        "status": "draft",
        "jit_enabled": payload.jit_enabled,
        "trust_email_claim": payload.trust_email_claim,
        "default_role": payload.default_role,
        "group_role_mapping": payload.group_role_mapping,
    }
    if isinstance(payload, OidcConnectionCreate):
        return SsoConnection(
            **common,
            protocol="oidc",
            issuer=payload.issuer,
            client_id=payload.client_id,
            scopes=list(dict.fromkeys(["openid", *payload.scopes])),
            claim_mapping=dict(payload.claim_mapping),
        )
    require_encrypted_transport(payload.server_url, payload.start_tls)
    row = SsoConnection(**common, protocol="ldap", issuer=f"ldap:{common['id']}")
    for field, column in LDAP_FIELDS.items():
        setattr(row, column, getattr(payload, field))
    return row


async def create_connection(
    mutation: SsoMutation,
    context: TenantAdminContext,
    payload: OidcConnectionCreate | LdapConnectionCreate,
) -> SsoConnectionRead:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    await validate_mapping(db, tenant.id, payload.group_role_mapping)
    row = _new_row(tenant, payload)
    db.add(row)
    await db.flush()
    secret = (
        payload.client_secret
        if isinstance(payload, OidcConnectionCreate)
        else payload.bind_password
    )
    await seal_secret(db, row, secret.get_secret_value())
    await db.flush()
    await db.refresh(row)
    await audit(
        mutation,
        tenant,
        audit_actions.TENANT_SSO_CONNECTION_CREATE,
        ("sso_connection", row.id, row.issuer),
        {"protocol": row.protocol},
    )
    result = connection_view(mutation.request, row)
    await db.commit()
    return result


async def _active_count(db: AsyncSession, tenant_id: UUID) -> int:
    count = await db.scalar(
        select(func.count(SsoConnection.id)).where(
            SsoConnection.tenant_id == tenant_id, SsoConnection.status == "active"
        )
    )
    return count or 0


async def _keep_required_policy(db: AsyncSession, tenant: Tenant, row: SsoConnection) -> None:
    if (
        row.status == "active"
        and sso_policy(tenant).mode == "required"
        and await _active_count(db, tenant.id) <= 1
    ):
        raise sso_error("sso_policy_incomplete")


async def update_connection(
    mutation: SsoMutation,
    context: TenantAdminContext,
    connection_id: UUID,
    payload: SsoConnectionUpdate,
) -> SsoConnectionRead:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    row = await owned_connection(db, tenant.id, connection_id, lock=True)
    changes = payload.model_dump(exclude_unset=True)
    own, secret_key = OWN_FIELDS[row.protocol], SECRET_KEYS[row.protocol]
    if changes.keys() - own.keys() - {secret_key} - COMMON_FIELDS:
        raise sso_error("sso_config_invalid")
    retest = False
    for field, column in own.items():
        value = changes.get(field)
        if field not in changes or (value is None and field not in NULLABLE):
            continue
        if field == "scopes" and isinstance(value, list):
            value = list(dict.fromkeys(["openid", *value]))
        if value != getattr(row, column):
            setattr(row, column, value)
            retest = retest or field != "claim_mapping"
    if payload.group_role_mapping is not None:
        await validate_mapping(db, tenant.id, payload.group_role_mapping)
        row.group_role_mapping = payload.group_role_mapping
    if payload.jit_enabled is not None:
        row.jit_enabled = payload.jit_enabled
    if payload.default_role is not None:
        row.default_role = payload.default_role
    if payload.trust_email_claim is not None and payload.trust_email_claim != row.trust_email_claim:
        row.trust_email_claim = payload.trust_email_claim
        retest = True  # changes whose accounts the IdP may claim
    secret = getattr(payload, secret_key)
    if secret is not None:
        await seal_secret(db, row, secret.get_secret_value())
        retest = True
    if row.protocol == "ldap":
        require_encrypted_transport(row.ldap_server_url, row.ldap_start_tls)
    reopen = payload.disabled is False and row.status == "disabled"
    if payload.disabled or (retest and row.status != "disabled"):
        await _keep_required_policy(db, tenant, row)
    if payload.disabled:
        row.status = "disabled"
    elif reopen or (retest and row.status != "disabled"):
        row.status, row.tested_at = "draft", None
    await db.flush()
    await db.refresh(row)
    await audit(
        mutation,
        tenant,
        audit_actions.TENANT_SSO_CONNECTION_UPDATE,
        ("sso_connection", row.id, row.issuer),
        {
            "fields": list[JsonValue](sorted(changes.keys() - {secret_key})),
            "secret_changed": secret is not None,
        },
    )
    result = connection_view(mutation.request, row)
    await db.commit()
    return result


async def delete_connection(
    mutation: SsoMutation, context: TenantAdminContext, connection_id: UUID
) -> None:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    row = await owned_connection(db, tenant.id, connection_id, lock=True)
    await _keep_required_policy(db, tenant, row)
    await audit(
        mutation,
        tenant,
        audit_actions.TENANT_SSO_CONNECTION_DELETE,
        ("sso_connection", row.id, row.issuer),
    )
    await db.delete(row)
    await db.commit()


async def activate_connection(
    mutation: SsoMutation, context: TenantAdminContext, connection_id: UUID
) -> SsoConnectionRead:
    db = mutation.db
    tenant = await locked_admin(mutation, context)
    row = await owned_connection(db, tenant.id, connection_id, lock=True)
    if row.status not in ("tested", "active"):
        raise sso_error("sso_connection_not_tested")
    if row.status == "tested":
        row.status = "active"
        await db.flush()
        await db.refresh(row)
        await audit(
            mutation,
            tenant,
            audit_actions.TENANT_SSO_CONNECTION_ACTIVATE,
            ("sso_connection", row.id, row.issuer),
        )
    result = connection_view(mutation.request, row)
    await db.commit()
    return result
