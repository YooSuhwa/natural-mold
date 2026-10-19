"""``prepare_authorized_runtime``: the single run authorization boundary (D20, 11.0; F-06).

Every run path that binds tools, credentials or a model to an agent goes through here:
``RuntimeAccessPlanner`` decides the agent and each link for the 11.1 subject, then the
existing builders (``build_tools_config`` with ``RunCredentialResolver``,
``build_agent_skills``) resolve only what the plan allows. The result is what the
executors consume: allowed tool configs (decrypted values travel in them as before,
F-10 deviation), skill descriptors, the subagent links to prepare and the excluded
capabilities for the run start event. Calling the builders outside this module is a
test failure (``tests/authz/test_f06_runtime_paths.py``) unless the call site is listed
there with its reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.authz.runtime_access_planner import RuntimeAccessPlan, RuntimeAccessPlanner

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.agent_runtime.identity import AgentRunIdentity
    from app.authz.org_context import OrgContext
    from app.authz.service import AuthzService
    from app.models.agent import Agent

# The 11.0 rows whose agents bind tools/credentials/models through this boundary.
RunPath = Literal[
    "chat",  # send, edit/regenerate, resume, protocol run.start/fork, side chat, queue
    "trigger",
    "api",
    "subagent",
]


@dataclass(frozen=True, slots=True)
class AuthorizedRuntime:
    path: RunPath
    plan: RuntimeAccessPlan
    tools_config: list[dict[str, Any]]
    agent_skills: list[dict[str, Any]]

    @property
    def sub_agent_links(self) -> tuple[Any, ...]:
        return self.plan.sub_agent_links

    @property
    def excluded_capabilities(self) -> list[dict[str, str]]:
        return self.plan.excluded_capabilities


async def prepare_authorized_runtime(
    agent: Agent,
    *,
    path: RunPath,
    context: OrgContext,
    db: AsyncSession,
    identity: AgentRunIdentity | None,
    conversation_id: str | None = None,
    authz: AuthzService | None = None,
    root: bool = True,
) -> AuthorizedRuntime:
    """``identity`` ``None``: a fixed run as the agent owner (triggers). ``root=False``:
    a child whose link its parent's plan already decided."""
    plan = await plan_authorized_runtime(
        agent, context=context, identity=identity, authz=authz, root=root
    )
    return await bind_authorized_runtime(
        plan, path=path, db=db, identity=identity, conversation_id=conversation_id, authz=authz
    )


async def plan_authorized_runtime(
    agent: Agent,
    *,
    context: OrgContext,
    identity: AgentRunIdentity | None,
    authz: AuthzService | None = None,
    root: bool = True,
) -> RuntimeAccessPlan:
    """The decision step alone (legacy: the owner check, no query), for callers that must
    run their own cheap checks before credentials are resolved."""
    return await RuntimeAccessPlanner(context, identity, authz).plan(agent, root=root)


def authorized_skills(plan: RuntimeAccessPlan) -> list[dict[str, Any]]:
    from app.services import chat_service  # call time: the facade's (patchable) builders

    return chat_service.build_agent_skills(plan.agent, plan.subject.context, plan=plan)


async def bind_authorized_runtime(
    plan: RuntimeAccessPlan,
    *,
    path: RunPath,
    db: AsyncSession,
    identity: AgentRunIdentity | None,
    conversation_id: str | None = None,
    authz: AuthzService | None = None,
) -> AuthorizedRuntime:
    """Resolve the allowed links' credentials (``RunCredentialResolver``) and skills."""
    from app.services import chat_service  # call time: the facade's (patchable) builders

    tools_config = await chat_service.build_tools_config(
        plan.agent,
        context=plan.subject.context,
        db=db,
        conversation_id=conversation_id,
        identity=identity,
        authz=authz,
        plan=plan,
    )
    return AuthorizedRuntime(path, plan, tools_config, authorized_skills(plan))


__all__ = [
    "AuthorizedRuntime",
    "RunPath",
    "authorized_skills",
    "bind_authorized_runtime",
    "plan_authorized_runtime",
    "prepare_authorized_runtime",
]
