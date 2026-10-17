"""F-03 review fixes (plan 10.6, 8.6): settled-state judging, retries and guards.

* undelivered outbox rows defer a would-be break; checks use ``HIGHER_CONSISTENCY``;
* the model credential is never silently re-granted;
* one failing break is skipped (savepoint), the rest of the organization proceeds;
* a break only applies while the link still has the delegator that was judged;
* finalized revocation markers are re-validated by the maintenance poll, which holds
  its watermark while OpenFGA is unavailable;
* catalog changes re-validate only the objects whose tuples were deleted.
"""

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import delegation_retry, delegation_validator
from app.authz.delegation_links import DelegationScope
from app.authz.delegation_retry import revalidate_finalized
from app.authz.delegation_sweep import revalidate_outbox
from app.authz.delegation_validator import validate_delegations
from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.resource_lifecycle import LifecycleChange, apply_lifecycle
from app.models.agent import Agent
from app.models.tool import AgentToolLink
from tests.authz.d04_support import OTHER_USER_ID, add_tool
from tests.authz.e01_support import add_agent, add_credential, seed
from tests.authz.f03_support import (
    ME,
    GraphFake,
    attach_tool,
    delegation_state,
    enforce,
    link_state,
    share,
)
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

SCOPE = DelegationScope(TEST_ORG_ID)


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)
    delegation_retry._state.clear()


async def _shared(db: AsyncSession, authz, name: str = "t"):
    agent, tool = await add_agent(db), await add_tool(db, owner=OTHER_USER_ID, name=name)
    grant = await share(db, authz, OTHER_USER_ID, "tool", tool.id, TEST_USER_ID)
    await attach_tool(db, authz, agent.id, tool.id, required=False)
    return agent, tool, grant


async def _validate(db: AsyncSession, authz):
    report = await validate_delegations(db, SCOPE, authz=authz, trigger="test")
    await db.commit()
    await apply_lifecycle(report.change, authz)
    return report


async def test_undelivered_outbox_defers_and_checks_use_higher_consistency(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent, tool, _ = await _shared(db, authz)
    fake.tuples.clear()
    request = GrantRequest("viewer", "user", TEST_USER_ID)  # queued, not yet in OpenFGA
    await GrantService().grant(
        db, unit_context(OTHER_USER_ID), GrantTarget("tool", tool.id), request
    )
    await db.commit()
    report = await _validate(db, authz)
    assert report.broken == [] and len(report.deferred) == 1
    assert await link_state(agent.id, tool.id) == ("active", None)
    assert set(fake.consistencies) == {"HIGHER_CONSISTENCY"}


async def test_model_credential_is_never_re_granted_silently(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent, credential = await add_agent(db), await add_credential(db)
    async with TestSession() as edit:
        row = await edit.get(Agent, agent.id)
        assert row is not None
        row.llm_credential_id, row.llm_credential_delegated_by = credential.id, TEST_USER_ID
        await edit.commit()
    fake.allowed.add((ME, "can_use", f"credential:{credential.id}"))
    assert (await _validate(db, authz)).repaired == []
    assert await delegation_state(agent.id, "credential", credential.id) == []


async def test_a_failing_break_is_skipped_and_the_rest_proceeds(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    first, tool_a, _ = await _shared(db, authz, "a")
    second, tool_b, _ = await _shared(db, authz, "b")
    fake.tuples.clear()
    real = delegation_validator.audit_service.record_event
    calls: list[object] = []

    async def flaky(*args, **kwargs):
        calls.append(kwargs.get("target_id"))
        if len(calls) == 1:
            raise RuntimeError("boom")
        return await real(*args, **kwargs)

    monkeypatch.setattr(delegation_validator.audit_service, "record_event", flaky)
    report = await _validate(db, authz)
    assert len(report.failed) == 1 and len(report.broken) == 1
    states = {await link_state(first.id, tool_a.id), await link_state(second.id, tool_b.id)}
    assert states == {("active", None), ("broken", "delegator_lost_access")}


async def test_break_requires_the_judged_delegator(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent, tool, _ = await _shared(db, authz)
    fake.tuples.clear()
    real = delegation_validator.decide

    async def reattached_meanwhile(session, *args):
        found = await real(session, *args)
        await session.execute(  # another editor re-delegated the link after our read
            update(AgentToolLink)
            .where(AgentToolLink.agent_id == agent.id)
            .values(delegated_by=OTHER_USER_ID)
        )
        return found

    monkeypatch.setattr(delegation_validator, "decide", reattached_meanwhile)
    await _validate(db, authz)
    assert await link_state(agent.id, tool.id) == ("active", None)


async def test_finalized_markers_are_revalidated_with_retry_on_outage(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent, tool, grant = await _shared(db, authz)
    change = await GrantService().revoke(db, unit_context(TEST_USER_ID), grant.id)
    await db.commit()
    await apply_lifecycle(LifecycleChange(change.outbox_ids), authz)  # marker finalized
    fake.unavailable = 1
    assert await revalidate_finalized(authz, maker=TestSession) == {TEST_ORG_ID: False}
    assert await link_state(agent.id, tool.id) == ("active", None)
    assert await revalidate_finalized(authz, maker=TestSession) == {TEST_ORG_ID: True}
    assert await link_state(agent.id, tool.id) == ("broken", "delegator_lost_access")


async def test_outbox_scope_only_touches_the_deleted_objects(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent_a, tool_a, grant_a = await _shared(db, authz, "a")
    agent_b, tool_b, _ = await _shared(db, authz, "b")
    change = await GrantService().revoke(db, unit_context(TEST_USER_ID), grant_a.id)
    await db.commit()
    await apply_lifecycle(LifecycleChange(change.outbox_ids), authz)
    fake.tuples.clear()  # tool_b is lost too, but its tuples were not in this change
    await revalidate_outbox(authz, TEST_ORG_ID, TEST_TENANT_ID, change.outbox_ids, trigger="t")
    assert await link_state(agent_a.id, tool_a.id) == ("broken", "delegator_lost_access")
    assert await link_state(agent_b.id, tool_b.id) == ("active", None)
