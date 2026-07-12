from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.main import create_app
from app.models.audit_event import AuditEvent
from app.models.user import User
from app.services import audit_service
from tests.conftest import override_get_db


def test_audit_event_table_contract() -> None:
    columns = AuditEvent.__table__.columns
    assert AuditEvent.__tablename__ == "audit_events"
    for name in (
        "id",
        "actor_type",
        "actor_user_id",
        "actor_api_key_id",
        "actor_email_snapshot",
        "owner_user_id",
        "action",
        "target_type",
        "target_id",
        "outcome",
        "reason_code",
        "request_id",
        "trace_id",
        "run_id",
        "metadata",
        "created_at",
    ):
        assert name in columns


@pytest.mark.asyncio
async def test_record_event_sanitizes_sensitive_metadata(db: AsyncSession) -> None:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email="owner@test.com", name="Owner"))
    await db.flush()

    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user_id,
        actor_email_snapshot="owner@test.com",
        owner_user_id=user_id,
        owner_email_snapshot="owner@test.com",
        action="credential.update",
        target_type="credential",
        target_id="cred-1",
        target_name_snapshot="OpenAI",
        outcome="success",
        metadata={"api_key": "sk-secret", "field_keys": ["api_key"]},
    )
    await db.commit()

    row = (await db.execute(select(AuditEvent))).scalar_one()
    assert row.event_metadata == {
        "api_key": "<redacted>",
        "field_keys": ["api_key"],
    }


@pytest.mark.asyncio
async def test_record_self_event_fills_identity_columns(db: AsyncSession) -> None:
    """record_self_event는 21개 self-action 감사 사이트의 단일 신원 채움 지점 —
    각 컬럼을 리터럴 기대값(독립 오라클)으로 고정한다. record_event 출력과의
    상호 비교(tautology)로 대체하지 말 것 (BE-P5 교훈)."""

    user_id = uuid.uuid4()
    db.add(User(id=user_id, email="self@test.com", name="Self"))
    await db.flush()
    run_id = uuid.uuid4()

    await audit_service.record_self_event(
        db,
        CurrentUser(id=user_id, email="self@test.com", name="Self"),
        action="agent.update",
        target_type="agent",
        target_id="agent-9",
        target_name="My Agent",
        outcome="failure",
        reason_code="tool_run_failed",
        reason_message="boom",
        run_id=run_id,
        metadata={"changed_fields": ["name"]},
    )
    await db.commit()

    row = (await db.execute(select(AuditEvent))).scalar_one()
    assert row.actor_type == "user"
    assert row.actor_user_id == user_id
    assert row.actor_email_snapshot == "self@test.com"
    assert row.owner_user_id == user_id
    assert row.owner_email_snapshot == "self@test.com"
    assert row.target_owner_user_id == user_id
    assert row.action == "agent.update"
    assert row.target_type == "agent"
    assert row.target_id == "agent-9"
    assert row.target_name_snapshot == "My Agent"
    assert row.outcome == "failure"
    assert row.reason_code == "tool_run_failed"
    assert row.reason_message == "boom"
    assert row.run_id == str(run_id)
    assert row.event_metadata == {"changed_fields": ["name"]}


@pytest.mark.asyncio
async def test_list_events_mine_uses_owner_or_actor(db: AsyncSession) -> None:
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    db.add_all(
        [
            User(id=owner_id, email="owner@test.com", name="Owner"),
            User(id=other_id, email="other@test.com", name="Other"),
        ]
    )
    await db.flush()

    await audit_service.record_event(
        db,
        actor_type="scheduler",
        owner_user_id=owner_id,
        action="trigger.scheduled_run",
        target_type="trigger",
        target_id="trigger-1",
        outcome="success",
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=owner_id,
        owner_user_id=other_id,
        action="marketplace.install",
        target_type="marketplace_item",
        target_id="item-1",
        outcome="success",
    )
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=other_id,
        owner_user_id=other_id,
        action="agent.create",
        target_type="agent",
        target_id="agent-1",
        outcome="success",
    )
    await db.commit()

    page = await audit_service.list_events(
        db,
        viewer_user_id=owner_id,
        viewer_is_super_user=False,
        scope="mine",
        limit=10,
    )
    actions = {event.action for event in page.items}
    assert actions == {"trigger.scheduled_run", "marketplace.install"}


async def _client_for_audit_user(user: CurrentUser) -> AsyncClient:
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    async def _override_user() -> CurrentUser:
        return user

    async def _no_csrf() -> None:
        return None

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[verify_csrf] = _no_csrf
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_audit_events_endpoint_rejects_all_scope_for_regular_user(
    db: AsyncSession,
) -> None:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email="regular@test.com", name="Regular"))
    await db.commit()

    async with await _client_for_audit_user(
        CurrentUser(
            id=user_id,
            email="regular@test.com",
            name="Regular",
            is_super_user=False,
        )
    ) as ac:
        response = await ac.get("/api/audit-events?scope=all")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_audit_events_endpoint_all_scope_for_super_user(
    db: AsyncSession,
) -> None:
    super_id = uuid.uuid4()
    other_id = uuid.uuid4()
    db.add_all(
        [
            User(id=super_id, email="admin@test.com", name="Admin", is_super_user=True),
            User(id=other_id, email="other@test.com", name="Other"),
        ]
    )
    await db.flush()
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=other_id,
        owner_user_id=other_id,
        action="agent.create",
        target_type="agent",
        target_id="agent-1",
        outcome="success",
    )
    await db.commit()

    async with await _client_for_audit_user(
        CurrentUser(
            id=super_id,
            email="admin@test.com",
            name="Admin",
            is_super_user=True,
        )
    ) as ac:
        response = await ac.get("/api/audit-events?scope=all")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["action"] == "agent.create"
