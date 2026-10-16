"""E-02: account deletion forces transfer first (plan 10.7 "사용자 삭제")."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.grants import GrantRequest, GrantService, GrantTarget
from app.authz.org_context import OrgContext
from app.authz.resource_lifecycle import LifecycleChange
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.user import User
from app.services import user_service
from app.services.user_service import delete_user
from tests.authz.e01_support import (
    OTHER_USER_ID,
    TEST_ORG_ID,
    TEST_USER_ID,
    add_agent,
    add_tool,
    audit_event,
    grants,
    seed,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID

CHECKPOINT = "app.agent_runtime.checkpointer.delete_thread"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _share_to_other(db: AsyncSession) -> UUID:
    tool = await add_tool(db)
    ctx = OrgContext(org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)
    request = GrantRequest("viewer", "user", OTHER_USER_ID)
    await GrantService().grant(db, ctx, GrantTarget("tool", tool.id), request)
    await db.commit()
    return tool.id


async def test_owned_resources_refuse_deletion_with_the_list_archived_included(
    db: AsyncSession,
) -> None:
    agent = await add_agent(db, owner=OTHER_USER_ID)
    row = await db.get(Agent, agent.id)
    assert row is not None
    row.archived_at = datetime.now(UTC).replace(tzinfo=None)  # m83 RESTRICT still applies
    await db.commit()
    tool_id = await _share_to_other(db)
    with patch(CHECKPOINT, AsyncMock()), pytest.raises(AppError) as captured:
        await delete_user(db, OTHER_USER_ID)
    assert (captured.value.code, captured.value.status) == ("resource_transfer_required", 409)
    assert captured.value.details == {"resources": {"agent": [str(agent.id)]}}
    assert [g.state for g in await grants(tool_id)] == ["active"]  # refused before revoking


async def test_deletion_revokes_the_accounts_grants_then_deletes(db: AsyncSession) -> None:
    tool_id = await _share_to_other(db)
    with patch(CHECKPOINT, AsyncMock()):
        change = await delete_user(db, OTHER_USER_ID)
    await db.commit()
    assert change.outbox_ids  # applied by the caller after commit (8.6 item 2)
    assert await db.get(User, OTHER_USER_ID) is None
    assert [g.state for g in await grants(tool_id)] == ["revoking"]
    event = await audit_event(tool_id, "grant.revoke")
    assert event.event_metadata is not None
    assert event.event_metadata["account_erasure"] == str(OTHER_USER_ID)


async def test_a_grant_written_after_the_revocation_pass_answers_conflict(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool_id = await _share_to_other(db)

    async def skipped(*_args: object, **_kwargs: object) -> LifecycleChange:
        return LifecycleChange()  # the concurrent share lands after the pass

    monkeypatch.setattr(user_service, "revoke_account_grants", skipped)
    with patch(CHECKPOINT, AsyncMock()), pytest.raises(AppError) as captured:
        await delete_user(db, OTHER_USER_ID)
    assert (captured.value.code, captured.value.status) == ("conflict", 409)
    await db.rollback()
    assert await db.get(User, OTHER_USER_ID) is not None
    assert [g.state for g in await grants(tool_id)] == ["active"]


async def test_unknown_account_is_a_no_op(db: AsyncSession) -> None:
    assert (await delete_user(db, uuid4())).outbox_ids == ()
