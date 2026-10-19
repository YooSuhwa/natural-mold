"""Agent permission re-checks on private conversation routes (plan 9.3, D-01).

Conversations stay PRIVATE (owner AND current org); only paths that start or
continue a run re-check ``agent#can_run``, debug traces ``agent#can_view_config``
and agent memory ``agent#can_edit``. Every call carries the existing legacy
decision so ``AUTHZ_MODE=legacy`` stays byte-identical (no extra query, no
sample), ``shadow`` records the legacy answer and ``enforce`` asks OpenFGA.
"""

from contextvars import ContextVar
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.run_decisions import open_request_memo, require_decided
from app.authz.runtime import get_authz, runtime_authz
from app.authz.scoping import private_scope_predicate
from app.authz.service import AuthzService, Verdict
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import Agent

AGENT_MIN_VISIBLE = "can_run"
_request_authz: ContextVar[AuthzService | None] = ContextVar("conversation_authz", default=None)


async def bind_request_authz(authz: AuthzService = Depends(get_authz)) -> AuthzService:
    """Route dependency: service-level checks in this request use the route's service.

    Keeps ``get_authz`` overrides and its shadow request stamp for decisions made
    deep in the run-preparation services; background work falls back to the process one.
    """
    _request_authz.set(authz)
    open_request_memo()  # F-06: one decision per (principal, object) in this request
    return authz


def _resolve(authz: AuthzService | None) -> AuthzService:
    return authz or _request_authz.get() or runtime_authz()


def current_authz() -> AuthzService:
    """The request's service when bound, else the process one (background runs)."""
    return _resolve(None)


async def require_agent_permission(
    context: OrgContext,
    agent_id: UUID,
    permission: str,
    *,
    authz: AuthzService | None = None,
    legacy: Verdict = Verdict.ALLOW,
    resource_org_id: UUID | None = None,
) -> None:
    """Decide ``permission`` on an Agent whose legacy answer is already known.

    Callers pass the legacy verdict reached by the existing gate (for execution
    paths: the Agent tree was just authorized in ``context``'s organization).
    """
    service = _resolve(authz)
    if service.mode == "legacy":
        return
    await require_decided(
        service,
        Principal.for_user(context),
        permission,
        f"agent:{agent_id}",
        min_visible=AGENT_MIN_VISIBLE,
        resource_org_id=resource_org_id or context.org_id,
        legacy=legacy,
        not_found=agent_not_found(),
    )


async def require_agent_run(
    context: OrgContext, agent_id: UUID, *, authz: AuthzService | None = None
) -> None:
    """Run start/continue after the legacy execution gate accepted the Agent tree."""
    await require_agent_permission(context, agent_id, "can_run", authz=authz)


async def queue_dispatch_denial(context: OrgContext, agent_id: UUID) -> AppError | None:
    """Queue dispatch re-check (11.6): the denial, or ``None`` when the caller may run."""
    try:
        await require_agent_run(context, agent_id)
    except AppError as exc:  # only the decision above raises here: 404/403/503 fail closed
        return exc
    return None


async def require_scoped_agent_permission(
    db: AsyncSession,
    context: OrgContext,
    agent_id: UUID,
    permission: str,
    *,
    authz: AuthzService | None = None,
) -> None:
    """Pre-check before a mutation; the legacy answer is the owner/org scope itself.

    The lookup runs only outside legacy mode, so legacy requests keep their queries.
    """
    authz = _resolve(authz)
    if authz.mode == "legacy":
        return
    row = (
        await db.execute(
            select(Agent.org_id, private_scope_predicate(Agent, context).label("owned"))
            .where(Agent.id == agent_id)
            .execution_options(autoflush=False)
        )
    ).one_or_none()
    legacy = Verdict.ALLOW if row is not None and row.owned else Verdict.NOT_FOUND
    await authz.require(
        Principal.for_user(context),
        permission,
        f"agent:{agent_id}",
        min_visible=AGENT_MIN_VISIBLE,
        resource_org_id=row.org_id if row is not None else None,
        legacy=legacy,
        not_found=agent_not_found(),
    )
