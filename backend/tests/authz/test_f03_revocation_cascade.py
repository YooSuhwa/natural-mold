"""F-03 done-when "회수 연쇄 통합 테스트" (plan 10.6, 10.2, 12.2).

The chain end to end: a share is revoked through ``DELETE /api/grants/{id}`` → the
tuple leaves OpenFGA (outbox apply) → the delegation validator runs after the commit →
the delegated link of a subagent breaks, its delegation grant is revoked and audited →
the parent agent's run is refused (``agent_not_ready``) because that subagent's broken
link is required, and run preparation no longer loads it. The daily sweep closes the
same gap when no event ran, and legacy mode never breaks anything.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.broken_links import require_runnable_links, use_runtime_link_view
from app.authz.delegation_sweep import run_delegation_sweep
from app.exceptions import AppError
from app.models.agent_subagent import AgentSubAgentLink
from app.models.audit_event import AuditEvent
from app.services import chat_service
from tests.authz.d04_support import OTHER_USER_ID, AuthzSwitch, add_tool, authz  # noqa: F401
from tests.authz.e01_support import add_agent, seed
from tests.authz.f03_support import (
    ME,
    GraphFake,
    attach_tool,
    delegation_state,
    enforce,
    link_state,
    share,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

CTX = unit_context(TEST_USER_ID)


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _chain(db: AsyncSession, monkeypatch: pytest.MonkeyPatch, fake: GraphFake):
    """P → A (subagent) → T (required tool shared to me by another member)."""
    service = enforce(monkeypatch, fake)
    parent, child = await add_agent(db), await add_agent(db)
    db.add(
        AgentSubAgentLink(
            parent_agent_id=parent.id, sub_agent_id=child.id, delegated_by=TEST_USER_ID
        )
    )
    await db.commit()
    tool = await add_tool(db, owner=OTHER_USER_ID)
    grant = await share(db, service, OTHER_USER_ID, "tool", tool.id, TEST_USER_ID)
    await attach_tool(db, service, child.id, tool.id, required=True)
    fake.allowed |= {(ME, "can_run", f"agent:{child.id}"), (ME, "can_revoke", f"tool:{tool.id}")}
    return service, parent, child, tool, grant


async def test_share_revoke_breaks_the_subagent_link_and_refuses_the_parent_run(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    authz: AuthzSwitch,  # noqa: F811
) -> None:
    fake = GraphFake()
    _, parent, child, tool, grant = await _chain(db, monkeypatch, fake)
    await require_runnable_links(db, parent.id)  # ready before the revocation
    authz.use(client=fake)  # the route decides with the same OpenFGA stand-in

    response = await client.delete(f"/api/grants/{grant.id}")  # I give up the share
    assert response.status_code == 204, response.text

    assert await link_state(child.id, tool.id) == ("broken", "delegator_lost_access")
    assert "active" not in await delegation_state(child.id, "tool", tool.id)
    async with TestSession() as check:
        actions = list(
            await check.scalars(
                select(AuditEvent.action).where(AuditEvent.target_id == str(child.id))
            )
        )
    assert "delegation.broken" in actions
    with pytest.raises(AppError) as refused:
        await require_runnable_links(db, parent.id)  # through the active P → A link
    assert refused.value.code == "agent_not_ready" and refused.value.status == 409
    assert refused.value.details == {
        "overall": "blocked",
        "items": [
            {
                "kind": "tool",
                "id": str(tool.id),
                "agent_id": str(child.id),
                "required": True,
                "result": "delegation_broken",
                "reason": "delegator_lost_access",
            }
        ],
    }
    async with TestSession() as run:
        use_runtime_link_view(run)
        loaded = await chat_service.get_agent_with_tools(run, child.id, CTX)
        assert loaded is not None and loaded.tool_links == []  # never bound to a run


async def test_daily_sweep_breaks_what_no_event_reported(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    service, _, child, tool, _ = await _chain(db, monkeypatch, fake)
    fake.tuples.clear()  # access lost without an event (e.g. a group change)
    reports = await run_delegation_sweep(service, maker=TestSession)
    assert [b.link.obj for r in reports.values() for b in r.broken] == [("tool", tool.id)]
    assert await link_state(child.id, tool.id) == ("broken", "delegator_lost_access")


async def test_legacy_revoke_leaves_links_alone(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    authz: AuthzSwitch,  # noqa: F811
) -> None:
    fake = GraphFake()
    _, parent, child, tool, grant = await _chain(db, monkeypatch, fake)
    authz.use(client=fake, mode="legacy")
    enforce(monkeypatch, fake, mode="legacy")
    assert (await client.delete(f"/api/grants/{grant.id}")).status_code == 204
    assert await link_state(child.id, tool.id) == ("active", None)
    await require_runnable_links(db, parent.id)
    assert await run_delegation_sweep(enforce(monkeypatch, fake, mode="legacy")) == {}
