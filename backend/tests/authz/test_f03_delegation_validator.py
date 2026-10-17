"""F-03 (plan 10.6, 8.6 위임 검증, D14): delegation_validator.

A delegator that can no longer use the object breaks the link (``broken``,
``delegator_lost_access``), loses the delegation grant and leaves a
``delegation.broken`` audit; kept links without a grant are repaired; nothing heals by
itself; pending revocations defer; legacy is a no-op and shadow only reports.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.delegation_links import DelegationScope
from app.authz.delegation_validator import validate_delegations
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.revocations import record_revocation
from app.models.audit_event import AuditEvent
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.tool import AgentToolLink
from tests.authz.d04_support import OTHER_USER_ID, add_server, add_tool
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
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

SCOPE = DelegationScope(TEST_ORG_ID)


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _shared_tool_link(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent, tool = await add_agent(db), await add_tool(db, owner=OTHER_USER_ID)
    grant = await share(db, authz, OTHER_USER_ID, "tool", tool.id, TEST_USER_ID)
    await attach_tool(db, authz, agent.id, tool.id, required=False)
    return fake, authz, agent, tool, grant


async def _validate(db: AsyncSession, authz, **kwargs):
    report = await validate_delegations(db, SCOPE, authz=authz, trigger="test", **kwargs)
    await db.commit()
    await apply_lifecycle(report.change, authz)  # as the callers do (8.6 item 2)
    return report


async def test_lost_access_breaks_the_link_revokes_the_grant_and_audits(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, authz, agent, tool, _ = await _shared_tool_link(db, monkeypatch)
    assert (await _validate(db, authz)).broken == []  # still usable: nothing changes
    assert await delegation_state(agent.id, "tool", tool.id) == ["active"]

    fake.tuples.clear()  # the share is gone in OpenFGA
    report = await _validate(db, authz)
    assert [(b.link.obj, b.reason) for b in report.broken] == [
        (("tool", tool.id), "delegator_lost_access")
    ]
    assert await link_state(agent.id, tool.id) == ("broken", "delegator_lost_access")
    assert await delegation_state(agent.id, "tool", tool.id) != ["active"]
    async with TestSession() as check:
        event = await check.scalar(
            select(AuditEvent).where(AuditEvent.action == "delegation.broken")
        )
    assert event is not None and event.target_id == str(agent.id)
    assert event.actor_type == "system" and event.tenant_id == TEST_TENANT_ID
    assert (event.event_metadata or {})["delegated_by"] == str(TEST_USER_ID)

    fake.allowed.add((ME, "can_use", f"tool:{tool.id}"))  # access restored
    assert (await _validate(db, authz)).repaired == []  # no auto-heal (10.6)
    assert await link_state(agent.id, tool.id) == ("broken", "delegator_lost_access")


async def test_legacy_is_a_no_op_and_shadow_or_dry_run_only_report(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, authz, agent, tool, _ = await _shared_tool_link(db, monkeypatch)
    fake.tuples.clear()
    assert (await _validate(db, authz, dry_run=True)).broken  # reported, not written
    assert await link_state(agent.id, tool.id) == ("active", None)
    shadow = enforce(monkeypatch, fake, mode="shadow")
    report = await _validate(db, shadow)
    assert report.broken == [] and len(report.unenforced) == 1
    legacy = enforce(monkeypatch, fake, mode="legacy")
    assert (await _validate(db, legacy)).unenforced == []
    assert await link_state(agent.id, tool.id) == ("active", None)
    assert await delegation_state(agent.id, "tool", tool.id) == ["active"]


async def test_pending_revocation_defers_instead_of_breaking(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, authz, agent, tool, _ = await _shared_tool_link(db, monkeypatch)
    fake.tuples.clear()
    await record_revocation(
        db,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        scope="membership",
        subject=ME,
        object=f"organization:{TEST_ORG_ID}",
    )
    await db.commit()
    report = await _validate(db, authz)
    assert report.broken == [] and len(report.deferred) == 1
    assert await link_state(agent.id, tool.id) == ("active", None)


async def test_kept_link_without_grant_is_repaired_or_broken(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent = await add_agent(db)
    usable, lost = await add_tool(db, name="u"), await add_tool(db, name="l")
    for tool in (usable, lost):  # links F-02 left without a grant (fixtures, discovery)
        db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id, delegated_by=TEST_USER_ID))
    await db.commit()
    fake.allowed.add((ME, "can_use", f"tool:{usable.id}"))
    report = await _validate(db, authz)
    assert [link.obj for link in report.repaired] == [("tool", usable.id)]
    assert await delegation_state(agent.id, "tool", usable.id) == ["active"]
    assert await link_state(agent.id, lost.id) == ("broken", "delegator_lost_access")
    assert await delegation_state(agent.id, "tool", lost.id) == []


async def test_mcp_tool_breaks_alone_and_the_server_grant_follows_the_last_link(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent = await add_agent(db)
    server, first = await add_server(db)
    second = McpTool(server_id=server.id, name="second", input_schema={})
    db.add(second)
    await db.flush()
    for tool in (first, second):
        db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=tool.id, delegated_by=TEST_USER_ID))
    await db.commit()
    fake.allowed |= {(ME, "can_invoke", f"mcp_tool:{t.id}") for t in (first, second)}
    await _validate(db, authz)  # repaired: one server grant for both links
    assert await delegation_state(agent.id, "mcp_server", server.id) == ["active"]

    fake.allowed.discard((ME, "can_invoke", f"mcp_tool:{second.id}"))  # tool blocked
    report = await _validate(db, authz)
    assert [b.link.link_id for b in report.broken] == [second.id]
    assert await delegation_state(agent.id, "mcp_server", server.id) == ["active"]

    fake.allowed.clear()
    await _validate(db, authz)
    assert await delegation_state(agent.id, "mcp_server", server.id) != ["active"]


async def test_shared_account_tool_needs_can_share_unless_owned(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D17: a tool holding a credential stays delegated only while the delegator may
    share it (the attach rule, re-checked)."""
    from tests.authz.e01_support import add_credential

    fake, authz, agent, tool, _ = await _shared_tool_link(db, monkeypatch)
    credential = await add_credential(db, owner=OTHER_USER_ID)
    async with TestSession() as edit:
        row = await edit.get(type(tool), tool.id)
        assert row is not None
        row.credential_id = credential.id
        await edit.commit()
    report = await _validate(db, authz)  # can_use (share tuple) but no can_share
    assert [b.link.obj for b in report.broken] == [("tool", tool.id)]
