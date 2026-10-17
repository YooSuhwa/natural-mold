"""Seeds and calls for the G-01 readiness tests: agent owner OTHER, caller TEST, THIRD lends."""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.service import AuthzService
from app.models.tool import AgentToolLink, Tool
from app.services import readiness_service
from app.services.readiness_service import Readiness
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.f05_support import CALLER, OWNER
from tests.authz.unit_scope_fixture import unit_context

AS_CALLER = f"user:{CALLER}"


def enforce(monkeypatch: pytest.MonkeyPatch, *allowed: tuple[str, str, str]) -> AuthzService:
    """Enforce every type; the run loader reads the same service (``runtime._current``)."""
    service = AuthzService(RecordingClient(allowed=frozenset(allowed)), mode="enforce")  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "_current", service)
    return service


def runnable(agent: Any, *, account: UUID | None = None) -> set[tuple[str, str, str]]:
    """The caller may run the agent; the run account may use its model."""
    who = account or (OWNER if agent.identity_mode == "fixed" else CALLER)
    return {
        (AS_CALLER, "can_run", f"agent:{agent.id}"),
        (f"user:{who}", "can_use", f"llm_model:{agent.model_id}"),
    }


async def link_tool(db: AsyncSession, agent: Any, tool: Tool, **link: Any) -> Tool:
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id, **link))
    await db.commit()
    return tool


async def assess(
    db: AsyncSession, agent: Any, authz: AuthzService, caller: UUID = CALLER
) -> Readiness:
    agent_id = agent if isinstance(agent, UUID) else agent.id
    db.expire_all()
    return await readiness_service.assess_agent(db, authz, unit_context(caller), agent_id)


def results(readiness: Readiness) -> dict[tuple[str, UUID | None], tuple[str, bool]]:
    return {(i.kind, i.id): (i.result, i.required) for i in readiness.items}
