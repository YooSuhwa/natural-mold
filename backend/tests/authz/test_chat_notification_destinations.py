"""The bell opens the exact version confirmed at completion in the recipient's current scope."""

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.dependencies import CurrentUser, get_current_user, get_org_context
from app.models.chat_notification_outbox import ChatNotificationOutbox
from app.models.notification import Notification
from app.models.organization import OrganizationMember
from app.services.chat_notification_outbox import drain_chat_notification_outbox
from tests.authz.chat_notification_result_fixture import (
    ORIGINAL,
    RESULT_NAME,
    UPDATED,
    GoalResult,
    goal_result,
)
from tests.authz.d04_support import OTHER_ORG_ID, OTHER_USER_ID, seed_scope
from tests.authz.h02_support import PATH, join_other_org
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID

__all__ = ["goal_result"]


async def test_completed_bell_downloads_verified_bytes_even_after_rewrite(
    client: AsyncClient, db: AsyncSession, goal_result: GoalResult
) -> None:
    result = goal_result
    notice = await result.deliver(db)
    assert notice.link == (
        f"/agents/{result.conversation.agent_id}/conversations/{result.conversation.id}#conversation-goal"
    )
    result.source.write_bytes(UPDATED)
    assert await result.recorder.collect_after_tool_result(
        tool_name="edit_file", tool_call_id="next"
    )
    await db.refresh(result.artifact)
    assert result.artifact.current_version_id != result.first_version.id

    [bell] = (await client.get(PATH)).json()["items"]
    assert bell["id"] == str(notice.id)
    assert bell["target"]["accessible"] and bell["payload"] == {}
    url = f"{PATH}/{notice.id}"
    destination = await client.get(f"{url}/destination")
    assert destination.status_code == 200 and "no-store" in destination.headers["cache-control"]
    assert destination.json() == {
        "agent_id": str(result.conversation.agent_id),
        "conversation_id": str(result.conversation.id),
        "run_id": str(result.run.id),
        "goal_id": str(result.goal.id),
        "job_id": None,
        "artifact_id": str(result.artifact.id),
        "version_id": str(result.first_version.id),
        "filename": RESULT_NAME,
    }
    response = await client.get(f"{url}/result")
    assert response.status_code == 200 and response.content == ORIGINAL
    assert RESULT_NAME in response.headers["content-disposition"]
    assert "no-store" in response.headers["cache-control"]


@pytest.mark.parametrize("change", ["missing", "corrupt", "deleted"])
async def test_result_unavailable_never_serves_replacement(
    client: AsyncClient, db: AsyncSession, goal_result: GoalResult, change: str
) -> None:
    result = goal_result
    notice = await result.deliver(db)
    result.source.write_bytes(UPDATED)
    assert await result.recorder.collect_after_tool_result(
        tool_name="edit_file", tool_call_id="next"
    )
    if change == "missing":
        result.stored_file.unlink()
    elif change == "corrupt":
        result.stored_file.write_bytes(b"x" * len(ORIGINAL))
    else:
        result.artifact.status = "deleted"
        await db.commit()
    response = await client.get(f"{PATH}/{notice.id}/result")
    assert response.status_code == 404
    assert ORIGINAL not in response.content and UPDATED not in response.content


@pytest.mark.parametrize("change", ["goal_version", "goal_revision", "membership"])
async def test_historical_bell_withholds_stale_or_revoked_result(
    client: AsyncClient, db: AsyncSession, goal_result: GoalResult, change: str
) -> None:
    result = goal_result
    notice = await result.deliver(db)
    if change == "goal_version":
        result.goal.version += 1
    elif change == "goal_revision":
        result.goal.revision += 1
    else:
        member = await db.get(OrganizationMember, (TEST_ORG_ID, TEST_USER_ID))
        assert member is not None
        member.status = "removed"
    await db.commit()
    [item] = (await client.get(PATH)).json()["items"]
    assert (item["target"]["accessible"], item["target"]["id"], item["link"]) == (False, None, None)
    for suffix in ("destination", "result"):
        response = await client.get(f"{PATH}/{notice.id}/{suffix}")
        assert response.status_code == 404
        assert RESULT_NAME not in response.text


async def test_same_owner_in_another_organization_cannot_open_copied_link(
    client: AsyncClient,
    db: AsyncSession,
    test_app: FastAPI,
    goal_result: GoalResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = await goal_result.deliver(db)
    await seed_scope(db)
    await join_other_org(db)
    monkeypatch.delitem(test_app.dependency_overrides, get_org_context)
    client.cookies.set(ACTIVE_ORG_COOKIE, str(OTHER_ORG_ID))
    assert (await client.get(PATH)).json()["items"] == []
    for suffix in ("destination", "result"):
        assert (await client.get(f"{PATH}/{notice.id}/{suffix}")).status_code == 404
    client.cookies.set(ACTIVE_ORG_COOKIE, str(TEST_ORG_ID))
    assert (await client.get(f"{PATH}/{notice.id}/result")).content == ORIGINAL


async def test_another_member_of_the_same_organization_cannot_follow_the_link(
    client: AsyncClient,
    db: AsyncSession,
    test_app: FastAPI,
    goal_result: GoalResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notice = await goal_result.deliver(db)
    await seed_scope(db)
    stranger = CurrentUser(id=OTHER_USER_ID, name="Other", email="other@test.com")
    monkeypatch.setitem(test_app.dependency_overrides, get_current_user, lambda: stranger)
    monkeypatch.delitem(test_app.dependency_overrides, get_org_context)
    client.cookies.set(ACTIVE_ORG_COOKIE, str(TEST_ORG_ID))
    assert (await client.get(PATH)).json()["items"] == []
    for suffix in ("destination", "result"):
        assert (await client.get(f"{PATH}/{notice.id}/{suffix}")).status_code == 404


@pytest.mark.parametrize("lifecycle", ["archived", "purged"])
async def test_preserved_history_keeps_its_exact_private_result(
    client: AsyncClient, db: AsyncSession, goal_result: GoalResult, lifecycle: str
) -> None:
    notice = await goal_result.deliver(db)
    conv = goal_result.conversation
    conv.agent_lifecycle_state = lifecycle
    conv.agent_archived_at_snapshot = datetime.now(UTC)
    if lifecycle == "purged":
        conv.agent_id = None
        conv.agent_purged_at = datetime.now(UTC)
    await db.commit()
    url = f"{PATH}/{notice.id}"
    target = (await client.get(f"{url}/destination")).json()
    assert target["agent_id"] == (str(conv.agent_id) if conv.agent_id else None)
    assert (await client.get(f"{url}/result")).content == ORIGINAL


@pytest.mark.parametrize(
    "error", ["all_passed", "result_passed", "mixed", "digest", "different_version"]
)
async def test_unverified_evidence_is_suppressed_before_publication(
    db: AsyncSession, goal_result: GoalResult, error: str
) -> None:
    result = goal_result
    payload = deepcopy(result.intent.payload)
    evidence = payload["evidence"]
    if error == "all_passed":
        evidence["all_passed"] = False
    elif error == "result_passed":
        evidence["checks"][0]["passed"] = False
    elif error == "mixed":
        evidence["checks"].append({"criterion_index": 1, "passed": False})
    elif error == "digest":
        evidence["checks"][0]["sha256"] = "0" * 64
    else:
        evidence["checks"][0]["version_id"] = str(uuid4())
    result.intent.payload = payload
    await result.enqueue(db)
    await db.commit()
    assert await drain_chat_notification_outbox(db, tenant_id=TEST_TENANT_ID) == 1
    await db.commit()
    assert await db.scalar(select(Notification.id)) is None
    assert (await db.scalars(select(ChatNotificationOutbox.status))).one() == "suppressed"
