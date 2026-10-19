"""Find or create the Moldy account for an SSO profile (plan 10.10 "계정 연결 규칙").

Users are found by ``(issuer, subject)``. Linking to an existing account by email —
and JIT creation — is allowed only when the email's domain is verified by the
connection's own company (prevents taking over someone else's account). An IdP that
says ``email_verified=false`` never links or creates, and an existing platform operator
account is never linked automatically. Attaching to an existing account also needs a
trusted email claim (``email_verified=true``, or no claim on a ``trust_email_claim``
connection); an account with a password and an unverified email must confirm once
with its password (``LinkPending`` → ``link_pending``). Every attach revokes the
account's existing refresh tokens.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.domains import email_domain
from app.auth.sso.errors import sso_error
from app.auth.sso.membership import Join, ensure_membership
from app.auth.sso.profile import SsoProfile
from app.models.organization import Tenant
from app.models.organization_identity import SsoConnection, TenantDomain, UserIdentity
from app.models.user import User
from app.services import auth_service, user_service


async def domain_verified_by(db: AsyncSession, tenant_id: UUID, email: str | None) -> bool:
    domain = email_domain(email) if email else None
    if domain is None:
        return False
    found = await db.scalar(
        select(TenantDomain.id).where(
            TenantDomain.domain == domain,
            TenantDomain.tenant_id == tenant_id,
            TenantDomain.status == "verified",
        )
    )
    return found is not None


def _usable_email(profile: SsoProfile) -> str:
    if not profile.email or profile.email_verified is False:
        raise sso_error("sso_email_missing")
    return profile.email


class LinkPending(Exception):
    """The account has a password but no verified email: confirm with one password login."""

    def __init__(self, user_id: UUID) -> None:
        super().__init__("sso link pending")
        self.user_id = user_id


def email_trusted(connection: SsoConnection, profile: SsoProfile) -> bool:
    """``email_verified=true``, or no claim at all on a ``trust_email_claim`` connection."""
    if profile.email_verified is None:
        return connection.trust_email_claim
    return profile.email_verified


def _require_attachable(connection: SsoConnection, profile: SsoProfile, user: User) -> None:
    if user.is_super_user:
        raise sso_error("sso_not_invited")  # a company IdP never claims an operator
    if not email_trusted(connection, profile):
        raise sso_error("sso_email_unverified")
    if user.hashed_password and user.email_verified_at is None:
        raise LinkPending(user.id)


async def _new_user(db: AsyncSession, email: str, profile: SsoProfile) -> User:
    user = User(
        email=email,
        name=(profile.name or email.split("@", 1)[0])[:100],
        hashed_password=None,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def sso_sign_in(
    db: AsyncSession, connection: SsoConnection, profile: SsoProfile, request: Request
) -> User:
    """The signed-in user, linked and enrolled; raises a typed ``sso_*`` error otherwise."""
    tenant = await db.get(Tenant, connection.tenant_id)
    if tenant is None or tenant.status != "active":
        raise sso_error("sso_unavailable")
    identity = await db.scalar(
        select(UserIdentity).where(
            UserIdentity.issuer == profile.issuer, UserIdentity.subject == profile.subject
        )
    )
    user = await db.get(User, identity.user_id) if identity is not None else None
    verified_domain = await domain_verified_by(db, tenant.id, profile.email)
    if identity is None:
        email = _usable_email(profile)
        if not verified_domain:
            raise sso_error("sso_domain_unverified")
        user = await user_service.get_by_email(db, email)
        if user is None:
            if not connection.jit_enabled:
                raise sso_error("sso_not_invited")
            user = await _new_user(db, email, profile)
        else:
            _require_attachable(connection, profile, user)
            await auth_service.revoke_all_refresh_tokens(db, user.id)  # sessions end on attach
        identity = UserIdentity(
            user_id=user.id,
            connection_id=connection.id,
            issuer=profile.issuer,
            subject=profile.subject,
        )
        db.add(identity)
    if user is None or not user.is_active:
        raise sso_error("sso_membership_blocked")
    may_join = verified_domain and profile.email_verified is not False
    await ensure_membership(
        Join(db, request, tenant, user), connection, profile.groups, may_join=may_join
    )
    identity.connection_id = connection.id
    identity.last_login_at = datetime.now(UTC)
    await db.flush()
    return user
