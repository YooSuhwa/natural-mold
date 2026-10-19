"""SSO-required enforcement for logins and refreshes (plan 10.10 "SSO 필수"; H-08).

A session records how it signed in (``refresh_tokens.auth_method``): ``password`` or
``sso:<tenant id>``; NULL (older rows) counts as password. For every company the user
is an active member of that requires SSO, the session is acceptable only when it
signed in through that company's own IdP, or the user is one of its break-glass
users *and still an active company admin*. Anything else is refused — fail closed:
a session is not company-bound, so a person in several companies who signed in via
company A (or with a password) is refused while company B requires its own IdP.
Agent API keys are a separate credential and are not affected (documented gap).
Break-glass MFA and break-glass notifications are not implemented (no MFA system).
"""

from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.sso.errors import sso_error
from app.auth.sso.policy import sso_policy
from app.authz import audit_actions
from app.authz.sso_members import active_admin_ids, active_member_tenants
from app.exceptions import AppError
from app.models.organization import Tenant
from app.models.user import User
from app.services import audit_service

PASSWORD: Final = "password"  # noqa: S105 - the sign-in method name, not a secret
_SSO_PREFIX: Final = "sso:"


def sso_method(tenant_id: UUID) -> str:
    return f"{_SSO_PREFIX}{tenant_id}"


@dataclass(frozen=True, slots=True)
class Verdict:
    blocked: list[Tenant] = field(default_factory=list)
    break_glass: list[Tenant] = field(default_factory=list)


async def evaluate(db: AsyncSession, user_id: UUID, method: str | None) -> Verdict:
    via = method[len(_SSO_PREFIX) :] if method and method.startswith(_SSO_PREFIX) else None
    verdict = Verdict()
    for tenant in await active_member_tenants(db, user_id):
        policy = sso_policy(tenant)
        if policy.mode != "required" or via == str(tenant.id):
            continue
        listed = user_id in policy.break_glass_user_ids
        if listed and user_id in await active_admin_ids(db, tenant.id, [user_id]):
            verdict.break_glass.append(tenant)
        else:
            verdict.blocked.append(tenant)
    return verdict


async def _audit(
    db: AsyncSession, request: Request, user: User, event: tuple[str, str, str | None, Tenant]
) -> None:
    action, outcome, reason, tenant = event
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        action=action,
        target_type="user",
        target_id=user.id,
        target_name_snapshot=user.email,
        outcome=outcome,
        reason_code=reason,
        request=request,
        tenant_id=tenant.id,
        metadata={"tenant_id": str(tenant.id)},
    )


async def login_gate(db: AsyncSession, user: User, request: Request, method: str) -> None:
    """Raise 403 ``sso_required`` or audit the break-glass use of this login.

    A refused password login is audited and committed here; a refused SSO login is
    audited by the SSO route's failure path.
    """
    verdict = await evaluate(db, user.id, method)
    if verdict.blocked:
        if method == PASSWORD:
            failure = (audit_actions.AUTH_LOGIN, "failure", "sso_required", verdict.blocked[0])
            await _audit(db, request, user, failure)
            await db.commit()
        raise sso_error("sso_required")
    for tenant in verdict.break_glass:
        used = (audit_actions.AUTH_SSO_BREAK_GLASS_LOGIN, "success", None, tenant)
        await _audit(db, request, user, used)


async def password_login_gate(db: AsyncSession, user: User, request: Request) -> None:
    await login_gate(db, user, request, PASSWORD)


async def refresh_session_gate(db: AsyncSession, user: User, method: str | None) -> None:
    """A refresh of a session that could not sign in today is refused (401)."""
    if (await evaluate(db, user.id, method or PASSWORD)).blocked:
        raise AppError(
            code="sso_required",
            message="이 회사는 회사 계정(SSO)으로 다시 로그인해야 합니다",
            status=401,
        )
