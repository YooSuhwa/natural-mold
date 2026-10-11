"""Native m83 accepts the malformed parent; actual JWT/CSRF mutation must fail closed."""

from __future__ import annotations

import os
from typing import Literal, assert_never
from uuid import UUID

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.models.memory import MemoryRecord
from app.models.model import Model
from app.models.user import User
from app.schemas.agent import AgentCreate
from app.schemas.memory import MemoryRecordCreate
from app.services import agent_service, memory_service
from app.services.chat.conversations import create_conversation
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("edge", ["agent", "conversation"])
async def test_native_http_memory_mutation_rejects_foreign_parent(
    monkeypatch: pytest.MonkeyPatch, edge: Literal["agent", "conversation"]
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "f1-r12-native@example.com",
                "name": "F1 native",
                "password": "dummy-f1-r12-password",
            },
        )
        assert registered.status_code == 201
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        user_id = UUID(registered.json()["user"]["id"])
        async with stack.sessions() as db:
            owned_database: str | None = await db.scalar(text("SELECT current_database()"))
            assert owned_database is not None and owned_database.startswith("moldy_pg_lane_orgs_")
            print(f"OWNED_LAB {owned_database}")
            user = await db.get(User, user_id)
            assert user is not None and user.last_active_org_id is not None
            actor = CurrentUser(
                id=user.id, email=user.email, name=user.name, is_super_user=user.is_super_user
            )
            own = await resolve_org_context(db, actor, cookie=str(user.last_active_org_id))
            model = Model(provider="openai", model_name="f1-fixture", display_name="F1 Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
            first_agent = await agent_service.create_agent(
                db, AgentCreate(name="Own", system_prompt="Fixture", model_id=model_id), own
            )
            first_conversation = await create_conversation(db, first_agent.id, context=own)
            record = await memory_service.create_memory_record(
                db,
                context=own,
                payload=MemoryRecordCreate(
                    scope="agent",
                    agent_id=first_agent.id,
                    source_conversation_id=first_conversation.id,
                    content="F1_NATIVE_BEFORE",
                ),
            )
            assert record is not None
            memory_id = record.id
        other = await select_second_company(stack, headers, "f1-r12-native@example.com")
        async with stack.sessions() as db:
            second_agent = await agent_service.create_agent(
                db, AgentCreate(name="Foreign", system_prompt="Fixture", model_id=model_id), other
            )
            second_conversation = await create_conversation(db, second_agent.id, context=other)
            match edge:
                case "agent":
                    stmt = (
                        update(MemoryRecord)
                        .where(MemoryRecord.id == memory_id)
                        .values(agent_id=second_agent.id)
                    )
                case "conversation":
                    stmt = (
                        update(MemoryRecord)
                        .where(MemoryRecord.id == memory_id)
                        .values(source_conversation_id=second_conversation.id)
                    )
                case unreachable:
                    assert_never(unreachable)
            await db.execute(stmt)
            await db.commit()
        selected = await stack.client.post(
            "/api/me/active-org", headers=headers, json={"org_id": str(own.org_id)}
        )
        assert selected.status_code == 200
        response = await stack.client.patch(
            f"/api/memories/{memory_id}", headers=headers, json={"content": "F1_NATIVE_AFTER"}
        )
        async with stack.sessions() as db:
            durable = await db.scalar(
                select(MemoryRecord.content).where(MemoryRecord.id == memory_id)
            )
        print(
            f"NATIVE_MEMORY_PARENT edge={edge} status={response.status_code} "
            f"durable_content={durable}"
        )
    admin = create_async_engine(
        make_url(os.environ["INTEGRATION_DATABASE_URL"]).set(drivername="postgresql+asyncpg")
    )
    try:
        async with admin.connect() as connection:
            remaining = await connection.scalar(
                text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                {"name": owned_database},
            )
            print(f"OWNED_LAB_CLEANUP name={owned_database} remaining={remaining}")
            assert remaining == 0
    finally:
        await admin.dispose()
    assert response.status_code == 404 and durable == "F1_NATIVE_BEFORE"
