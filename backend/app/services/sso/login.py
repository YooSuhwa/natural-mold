"""Shared login plumbing: usable connections, the OIDC client and cookie issuance.

A successful SSO login mints exactly the cookies of the password login (8.1).
"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import set_active_org_cookie, set_auth_cookies
from app.auth.sso.errors import sso_error
from app.auth.sso.oidc import OidcClient, discover, exchange_code, verify_id_token
from app.auth.sso.profile import SsoProfile, oidc_profile
from app.auth.sso.secret_box import open_secret
from app.auth.sso.session_gate import login_gate, sso_method
from app.auth.sso.state import LoginTransaction
from app.authz import audit_actions
from app.models.organization import Tenant
from app.models.organization_identity import SsoConnection
from app.models.user import User
from app.services import audit_service, auth_service, user_service
from app.services.sso.common import callback_url


async def login_connection(
    db: AsyncSession, connection_id: UUID, protocol: str, *, testing: bool = False
) -> SsoConnection:
    """An active connection of an active company (tests also accept draft/tested)."""
    row = await db.get(SsoConnection, connection_id)
    usable = ("draft", "tested", "active") if testing else ("active",)
    if row is None or row.protocol != protocol or row.status not in usable:
        raise sso_error("sso_unavailable")
    tenant = await db.get(Tenant, row.tenant_id)
    if tenant is None or tenant.status != "active":
        raise sso_error("sso_unavailable")
    return row


async def oidc_client(db: AsyncSession, request: Request, row: SsoConnection) -> OidcClient:
    secret = await open_secret(db, row)
    if not row.client_id or not secret:
        raise sso_error("sso_config_invalid")
    return OidcClient(row.client_id, secret, callback_url(request, row.id))


async def oidc_callback_profile(
    db: AsyncSession,
    request: Request,
    row: SsoConnection,
    grant: tuple[str, LoginTransaction],
) -> SsoProfile:
    """Code → token → verified ID token → profile (every step fails closed)."""
    code, transaction = grant
    provider = await discover(row.issuer)
    client = await oidc_client(db, request, row)
    token = await exchange_code(provider, client, code=code, verifier=transaction.verifier)
    claims = await verify_id_token(
        provider, token, client_id=client.client_id, nonce=transaction.nonce
    )
    return oidc_profile(row.issuer, claims, row.claim_mapping or {})


@dataclass(frozen=True, slots=True)
class ConnectionRef:
    """Plain ids that survive a rollback (ORM rows expire on rollback)."""

    id: UUID
    tenant_id: UUID
    protocol: str


def connection_ref(row: SsoConnection) -> ConnectionRef:
    return ConnectionRef(row.id, row.tenant_id, row.protocol)


async def audit_login(
    db: AsyncSession,
    request: Request,
    row: ConnectionRef,
    outcome: tuple[User | None, str | None],
) -> None:
    user, reason = outcome
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id if user else None,
        actor_email_snapshot=user.email if user else None,
        owner_user_id=user.id if user else None,
        action=audit_actions.AUTH_SSO_LOGIN,
        target_type="sso_connection",
        target_id=row.id,
        outcome="failure" if reason else "success",
        reason_code=reason,
        request=request,
        tenant_id=row.tenant_id,
        metadata={"tenant_id": str(row.tenant_id), "protocol": row.protocol},
    )


async def finish_login(
    db: AsyncSession, request: Request, response: Response, user: User, row: SsoConnection
) -> str:
    """Issue the JWT cookies on ``response``; returns the CSRF token.

    The session is marked ``sso:<company>``; other SSO-required companies of the user
    still refuse it (``session_gate``).
    """
    method = sso_method(row.tenant_id)
    await login_gate(db, user, request, method)
    access, refresh, csrf = await auth_service.issue_tokens(db, user, request, method)
    await user_service.record_login_success(db, user, ip=auth_service.client_ip(request))
    await audit_login(db, request, connection_ref(row), (user, None))
    active_org = user.last_active_org_id
    await db.commit()
    set_auth_cookies(response, access_token=access, refresh_token=refresh, csrf_token=csrf)
    if active_org is not None:
        set_active_org_cookie(response, active_org)
    return csrf


async def record_failure(
    db: AsyncSession, request: Request, row: ConnectionRef | None, code: str
) -> None:
    await db.rollback()
    if row is None:
        return
    await audit_login(db, request, row, (None, code))
    await db.commit()
