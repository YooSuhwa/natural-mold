"""H-02: runtime call sites — trigger.blocked (F-08), delegation.broken (F-03),
connection.owner_needed (F-07)."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import notification_events as events
from app.authz import runtime
from app.authz.delegation_links import DelegationScope
from app.authz.delegation_validator import validate_delegations
from app.authz.resource_lifecycle import apply_lifecycle
from app.authz.service import AuthzService
from app.models.agent_trigger import AgentTrigger
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.d04_support import OTHER_USER_ID, add_tool
from tests.authz.e01_support import add_agent, add_credential, seed
from tests.authz.f03_support import GraphFake, attach_tool, enforce, share
from tests.authz.h02_support import join_other_org, rows
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _fire(trigger_id: object) -> None:
    with (
        patch("app.agent_runtime.trigger_executor.execute_agent_invoke", AsyncMock()),
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id))


async def test_blocked_trigger_notifies_its_owner_once_per_streak(db, monkeypatch) -> None:
    agent = await add_agent(db)
    trigger = AgentTrigger(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_id=agent.id,
        user_id=TEST_USER_ID,
        name="주간 휴가 현황",
        trigger_type="interval",
        schedule_config={"interval_minutes": 10},
        input_message="go",
        status="active",
        run_count=0,
        auto_pause_after_failures=None,
    )
    db.add(trigger)
    await db.commit()
    service = AuthzService(RecordingClient(allowed=frozenset()), mode="enforce")  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "_current", service)
    await _fire(trigger.id)
    await _fire(trigger.id)
    [row] = await rows(TEST_USER_ID)
    assert (row.kind, row.target_type, row.target_id, row.link) == (
        "trigger.blocked",
        "agent",
        agent.id,
        f"/agents/{agent.id}/settings",
    )
    assert row.payload == {
        "target_name": "A",
        "reason": "permission_revoked",
        "trigger_name": "주간 휴가 현황",
    }


async def test_broken_delegation_notifies_agent_managers_without_the_resource(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = GraphFake()
    authz = enforce(monkeypatch, fake)
    agent = await add_agent(db)
    tool = await add_tool(db, owner=OTHER_USER_ID, name="비공개 도구")
    second = await add_tool(db, owner=OTHER_USER_ID, name="두 번째")
    for linked in (tool, second):
        await share(db, authz, OTHER_USER_ID, "tool", linked.id, TEST_USER_ID)
        await attach_tool(db, authz, agent.id, linked.id, required=False)
    fake.tuples.clear()
    lookups = AsyncMock(wraps=events.resource_managers)
    monkeypatch.setattr(events, "resource_managers", lookups)
    report = await validate_delegations(
        db, DelegationScope(TEST_ORG_ID), authz=authz, trigger="test"
    )
    await db.commit()
    await apply_lifecycle(report.change, authz)
    [row] = await rows(TEST_USER_ID, kind="delegation.broken")
    assert (row.target_type, row.target_id) == ("agent", agent.id)
    assert row.payload == {
        "target_name": "A",
        "reason": "delegator_lost_access",
        "resource_type": "tool",
    }
    assert str(tool.id) not in str(row.payload) and await rows(OTHER_USER_ID) == []
    assert lookups.await_count == 1  # managers cached for the validator run


async def test_owner_needed_goes_to_the_credential_owner_once_per_run(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_runtime.runtime import connection_guard_items as items

    monkeypatch.setattr("app.authz.notification_events.async_session", TestSession)
    credential = await add_credential(db, owner=OTHER_USER_ID)
    item = {
        "resource_type": "tool",
        "resource_name": "slack_post",
        "credential_id": str(credential.id),
        "tool_names": ["slack_post"],
    }
    tenants = {str(credential.id): str(TEST_TENANT_ID)}
    await items.notify_owner([item, item], run="h02-run-1", tenants=tenants)
    await items.notify_owner([item], run="h02-run-1", tenants=tenants)
    await items.notify_owner([item], run="h02-run-2", tenants=tenants)  # merged for 24 hours
    await items.notify_owner([{"credential_id": "not-a-uuid"}], run="h02-run-3")
    await items.notify_owner([item], run="h02-run-4")  # company unknown: skipped
    [row] = await rows(OTHER_USER_ID)
    assert (row.kind, row.target_type, row.target_id, row.actor_id) == (
        "connection.owner_needed",
        "credential",
        credential.id,
        None,
    )
    assert row.payload == {"target_name": "Slack"}  # no runner, agent or tool names


async def test_org_less_credential_resolves_the_owners_only_org(db: AsyncSession) -> None:
    """The schema requires an org on user-owned rows; this guards the fallback anyway."""
    from app.models.credential import Credential

    loose = Credential(user_id=OTHER_USER_ID, tenant_id=TEST_TENANT_ID, org_id=None)
    assert await events._personal_org(db, loose) == TEST_ORG_ID  # noqa: SLF001
    await join_other_org(db, OTHER_USER_ID)
    assert await events._personal_org(db, loose) is None  # ambiguous: skipped, logged


async def test_owner_notice_is_retried_after_a_failed_or_slow_write(monkeypatch) -> None:
    from app.agent_runtime.runtime import connection_guard_items as items

    item = {"credential_id": "c-retry", "resource_name": "Slack"}
    sent = AsyncMock(side_effect=[False, True, True])
    monkeypatch.setattr(items, "owner_needed", sent)
    await items.notify_owner([item], run="h02-retry")  # write failed: not marked
    await items.notify_owner([item], run="h02-retry")
    await items.notify_owner([item], run="h02-retry")  # marked: skipped
    assert sent.await_count == 2

    async def slow(*_: object) -> bool:
        await asyncio.sleep(5)
        return True

    monkeypatch.setattr(items, "owner_needed", slow)
    monkeypatch.setattr(items, "OWNER_NOTIFY_TIMEOUT", 0.05)
    await items.notify_owner([item], run="h02-slow")  # returns quickly, not marked
    assert ("h02-slow", "c-retry") not in items._NOTIFIED  # noqa: SLF001
