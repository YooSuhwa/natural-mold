"""Builder Assistant tool decisions through ``AuthzService`` (plan 11.7, 10.6; D-08).

Assistant tools run inside the agent runtime, outside any request, so they take the
process service from ``runtime_authz()`` at call time. Each tool call re-checks the
actor on the target agent: reads ``can_view_config``, writes ``can_edit``.

* legacy: no extra query; the tool's existing owner-scoped load stays the decision
  (a missing row is the tool's existing "not found" message).
* shadow: the owner-scoped row is the legacy verdict; a sample is recorded.
* enforce: OpenFGA decides; an allowed non-owner reads the agent in its owner's
  scope (like the agent detail route), a denial is the same "not found" message.

Links the Assistant adds (10.6) keep the actor-scoped lookup as the candidate set and
are confirmed with the resource permission; OpenFGA can only narrow that set until
delegation grants let a link point at a resource the agent owner cannot see.
"""

from collections.abc import Callable, Iterable
from operator import attrgetter
from typing import Any, Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime as authz_runtime
from app.authz.agent_access import is_owner, owner_context, require_agent
from app.authz.org_context import OrgContext
from app.authz.principal import Principal
from app.authz.scoping import private_scope_predicate
from app.error_codes import agent_not_found
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger

READ: Final = "can_view_config"
WRITE: Final = "can_edit"
# Permission the actor needs on a resource it links to an agent (plan 10.6).
LINK_PERMISSIONS: Final[dict[str, str]] = {
    "tool": "can_use",
    "skill": "can_use",
    "mcp_tool": "can_invoke",
    "agent": "can_run",
}
_DENIED: Final = frozenset({403, 404})

# ORM rows and ``select(Model.id, ...)`` result rows both expose ``id``.
_ROW_ID: Final[Callable[[Any], UUID]] = attrgetter("id")


async def assistant_target_scope(
    db: AsyncSession, ctx: OrgContext, agent_id: UUID, permission: str
) -> OrgContext | None:
    """The scope to load the target agent in, or ``None`` when the actor is denied."""
    authz = authz_runtime.runtime_authz()
    if authz.mode == "legacy":
        return ctx
    owned = await db.scalar(
        select(Agent)
        .where(Agent.id == agent_id, private_scope_predicate(Agent, ctx))
        .execution_options(autoflush=False)
    )
    try:
        row = await require_agent(
            db, authz, ctx, agent_id, permission, legacy=owned, not_found=agent_not_found()
        )
    except AppError as denied:
        if denied.status in _DENIED:
            return None
        raise
    return ctx if is_owner(row, ctx) else owner_context(row)


async def keep_linkable[R](ctx: OrgContext, fga_type: str, rows: Iterable[R]) -> list[R]:
    """Confirm actor-scoped link candidates with the 10.6 resource permission (order kept)."""
    page = list(rows)
    objects = [f"{fga_type}:{_ROW_ID(row)}" for row in page]
    kept = set(
        await authz_runtime.runtime_authz().filter_visible(
            Principal.for_user(ctx), LINK_PERMISSIONS[fga_type], objects, legacy=objects
        )
    )
    return [row for row, obj in zip(page, objects, strict=True) if obj in kept]


async def actor_active_trigger_count(db: AsyncSession, ctx: OrgContext, agent_id: UUID) -> int:
    """Active schedules on ``agent_id`` that block the switch to ``per_user`` identity.

    Every active trigger counts (as in ``agent_update_service``); legacy keeps its
    owner-only SQL, which is the same set because only the owner reaches the agent.
    """
    legacy = authz_runtime.runtime_authz().mode == "legacy"
    owner_only = (AgentTrigger.user_id == ctx.user_id,) if legacy else ()
    stmt = (
        select(func.count())
        .select_from(AgentTrigger)
        .where(AgentTrigger.agent_id == agent_id, *owner_only, AgentTrigger.status == "active")
    )
    return int(await db.scalar(stmt) or 0)
