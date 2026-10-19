"""Agent route decisions with the legacy owner answer as fallback (plan 9.1, 8.4).

Routes keep loading agents through the private owner scope (the legacy decision).
``require_agent`` hands that answer to ``AuthzService.require``; outside legacy mode
it also loads the in-organization row so shadow samples and enforce decisions see
agents shared through grants. List candidates come from ``candidates.candidate_ids``
and are confirmed page by page with ``filter_visible`` (plan 8.10).
"""

from collections.abc import Sequence
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import LoaderOption

from app.authz import denials
from app.authz.candidates import candidate_ids
from app.authz.client import AuthzUnavailable
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.service import AuthzService, Verdict
from app.exceptions import AppError
from app.models.agent import AGENT_RUNTIME_PROFILE_STANDARD, Agent

# Response ``my_permissions`` vocabulary for agents (model.fga ``type agent``).
AGENT_PERMISSIONS: Final[tuple[str, ...]] = (
    "can_run",
    "can_view_config",
    "can_clone",
    "can_edit",
    "can_share",
    "can_deploy",
    "can_delete",
)
_LEVELS: Final = (
    ("can_share", "manager"),
    ("can_edit", "editor"),
    ("can_view_config", "viewer"),
    ("can_run", "consumer"),
)


def agent_object(agent_id: UUID) -> str:
    return f"agent:{agent_id}"


def owner_context(agent: Agent) -> OrgContext:
    """The owner's scope: a shared agent is presented with its owner's links."""
    return OrgContext(org_id=agent.org_id, tenant_id=agent.tenant_id, user_id=agent.user_id)


def is_owner(agent: Agent, ctx: OrgContext) -> bool:
    """The legacy rule: only the owner, inside the agent's organization, may act."""
    return (agent.user_id, agent.org_id, agent.tenant_id) == (
        ctx.user_id,
        ctx.org_id,
        ctx.tenant_id,
    )


def access_level(permissions: Sequence[str]) -> str | None:
    """Sharing level (plan 10.1) implied by ``my_permissions``."""
    return next((level for perm, level in _LEVELS if perm in permissions), None)


async def candidate_agent_ids(db: AsyncSession, ctx: OrgContext) -> list[UUID]:
    """List candidates (owned or granted; standard, not archived), newest first."""
    return await candidate_ids(
        db,
        Agent,
        "agent",
        ctx,
        where=(
            Agent.org_id == ctx.org_id,
            Agent.runtime_profile == AGENT_RUNTIME_PROFILE_STANDARD,
            Agent.archived_at.is_(None),
        ),
        order_by=(Agent.created_at.desc(),),
    )


async def require_agent(
    db: AsyncSession,
    authz: AuthzService,
    ctx: OrgContext,
    agent_id: UUID,
    permission: str,
    *,
    legacy: Agent | None,
    not_found: AppError,
    min_visible: str = "can_run",
    options: Sequence[LoaderOption] = (),
) -> Agent:
    """Raise the 8.4 response unless allowed; ``legacy`` is the owner-scoped row."""
    row = legacy
    if row is None and authz.mode != "legacy":
        row = await db.scalar(
            select(Agent)
            .where(
                Agent.id == agent_id,
                Agent.org_id == ctx.org_id,
                Agent.tenant_id == ctx.tenant_id,
            )
            .options(*options)
            .execution_options(autoflush=False, populate_existing=True)
        )
    await authz.require(
        Principal.for_user(ctx),
        permission,
        agent_object(agent_id),
        min_visible=min_visible,
        resource_org_id=row.org_id if row is not None else None,
        legacy=Verdict.ALLOW if legacy is not None else Verdict.NOT_FOUND,
        not_found=not_found,
    )
    if row is None:  # unreachable: require raised for a missing row
        raise not_found
    return row


async def agents_permissions(
    authz: AuthzService, ctx: OrgContext, rows: Sequence[tuple[UUID, bool]]
) -> dict[UUID, list[str]]:
    """``my_permissions`` per ``(agent_id, owned)``; one BatchCheck for the whole page.

    Not enforced: the legacy owner answer (owner holds every permission), shadow-sampled.
    """
    p = Principal.for_user(ctx)
    perms: list[str] = list(AGENT_PERMISSIONS)
    if not authz.enforces("agent"):
        return {
            i: await authz.my_permissions(p, agent_object(i), perms, legacy=perms if owned else [])
            for i, owned in rows
        }
    if not rows:
        return {}
    try:
        answers = await authz.batch_check(
            p, [(perm, agent_object(i)) for i, _ in rows for perm in perms]
        )
    except AuthzUnavailable as error:
        raise denials.authz_unavailable() from error
    return {i: [perm for perm in perms if answers[(perm, agent_object(i))]] for i, _ in rows}


async def visible_agent_ids(
    authz: AuthzService, ctx: OrgContext, candidates: Sequence[UUID], *, legacy: Sequence[UUID]
) -> list[UUID]:
    """Confirm candidates with ``can_run``; ``legacy`` is the owner-scoped list."""
    kept = await authz.filter_visible(
        Principal.for_user(ctx),
        "can_run",
        [agent_object(i) for i in candidates],
        legacy=[agent_object(i) for i in legacy],
    )
    allowed = set(kept)
    return [i for i in candidates if agent_object(i) in allowed]
