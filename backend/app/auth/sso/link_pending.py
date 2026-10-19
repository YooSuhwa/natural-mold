"""Link-pending: confirm an SSO identity for a password account by one password login.

When an IdP/directory identity matches an existing account that has a password but
no verified email (``users.email_verified_at``), nothing is attached. The browser
gets a short-lived signed HttpOnly cookie (scoped to ``/api/auth/login``) naming the
identity; the next successful password login of *that* account attaches it, revokes
the account's other sessions, applies the company membership mapping and signs the
session in as that company's SSO (so the company's SSO-required rule is satisfied).
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_transaction, encode_transaction
from app.auth.sso.membership import Join, ensure_membership
from app.auth.sso.profile import SsoProfile
from app.auth.sso.provisioning import domain_verified_by
from app.auth.sso.session_gate import PASSWORD, sso_method
from app.authz import audit_actions
from app.config import settings
from app.models.organization import Tenant
from app.models.organization_identity import SsoConnection, UserIdentity
from app.models.user import User
from app.services import audit_service, auth_service

LINK_COOKIE: Final = "moldy_sso_link"
LINK_PATH: Final = "/api/auth/login"
_AUDIENCE: Final = "moldy-sso-link"
_TTL: Final = timedelta(minutes=10)


def set_link_cookie(
    response: Response, user_id: UUID, connection_id: UUID, profile: SsoProfile
) -> None:
    claims: dict[str, object] = {
        "uid": str(user_id),
        "cid": str(connection_id),
        "iss_": profile.issuer,
        "sub_": profile.subject,
        "grp": list(profile.groups[:50]),
    }
    response.set_cookie(
        LINK_COOKIE,
        encode_transaction(claims, audience=_AUDIENCE, ttl=_TTL),
        max_age=int(_TTL.total_seconds()),
        path=LINK_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_link_cookie(response: Response) -> None:
    response.delete_cookie(LINK_COOKIE, path=LINK_PATH)


def _claims(request: Request) -> dict[str, object] | None:
    cookie = request.cookies.get(LINK_COOKIE)
    if not cookie:
        return None
    return decode_transaction(cookie, audience=_AUDIENCE)


async def pending_link_method(db: AsyncSession, request: Request, user: User) -> str:
    """Attach the pending identity for ``user`` → ``sso:<tenant>``; otherwise ``password``."""
    claims = _claims(request)
    if claims is None or claims.get("uid") != str(user.id):
        return PASSWORD
    try:
        connection = await db.get(SsoConnection, UUID(str(claims["cid"])))
    except (KeyError, ValueError):
        return PASSWORD
    tenant = await db.get(Tenant, connection.tenant_id) if connection else None
    if connection is None or connection.status != "active" or tenant is None:
        return PASSWORD
    if tenant.status != "active" or not await domain_verified_by(db, tenant.id, user.email):
        return PASSWORD
    issuer, subject = str(claims.get("iss_")), str(claims.get("sub_"))
    taken = await db.scalar(
        select(UserIdentity).where(UserIdentity.issuer == issuer, UserIdentity.subject == subject)
    )
    if taken is not None:
        return sso_method(tenant.id) if taken.user_id == user.id else PASSWORD
    await auth_service.revoke_all_refresh_tokens(db, user.id)
    db.add(
        UserIdentity(
            user_id=user.id,
            connection_id=connection.id,
            issuer=issuer,
            subject=subject,
            last_login_at=datetime.now(UTC),
        )
    )
    raw_groups = claims.get("grp")
    groups = tuple(str(g) for g in raw_groups) if isinstance(raw_groups, list) else ()
    await ensure_membership(Join(db, request, tenant, user), connection, groups, may_join=True)
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        action=audit_actions.AUTH_SSO_LINK,
        target_type="sso_connection",
        target_id=connection.id,
        outcome="success",
        request=request,
        tenant_id=tenant.id,
        metadata={"tenant_id": str(tenant.id), "protocol": connection.protocol},
    )
    await db.flush()
    return sso_method(tenant.id)
