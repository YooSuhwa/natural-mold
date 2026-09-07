from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_csrf_token
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.exception_handlers import register_exception_handlers
from app.models.conversation import Conversation
from app.models.user import User
from app.routers import conversation_pinned_summaries, conversations
from app.services.conversation_pinned_summary_service import SummarySource
from tests.conftest import TEST_USER_ID, TestSession, seed_agent


def test_pinned_summary_routes_are_registered_on_conversation_router() -> None:
    routes = [
        route
        for route in conversations.router.routes
        if isinstance(route, APIRoute) and route.path.endswith("/pinned-summary")
    ]
    assert {route.path for route in routes} == {
        "/api/conversations/{conversation_id}/pinned-summary"
    }
    assert {method for route in routes for method in route.methods or set()} == {
        "DELETE",
        "GET",
        "PUT",
    }


def test_summary_sources_match_visible_cross_turn_message_identities() -> None:
    # Given: LangGraph reuses one assistant id across turns and replaces it within a turn.
    messages = (
        HumanMessage(id="user-1", content="First"),
        AIMessage(id="assistant-shared", content="First answer"),
        HumanMessage(id="user-2", content="Second"),
        AIMessage(id="assistant-shared", content="Partial second answer"),
        AIMessage(id="assistant-shared", content="Final second answer"),
    )

    # When
    sources = conversation_pinned_summaries._sources_for_messages(messages, "checkpoint-2")

    # Then: identities and same-turn latest-value behavior mirror assistant-ui's message list.
    assistant_sources = [source for source in sources if source.role == "assistant"]
    assert [(source.message_id, source.text) for source in assistant_sources] == [
        ("assistant-shared", "First answer"),
        ("assistant-shared::moldy-turn-1", "Final second answer"),
    ]


def test_summary_sources_preserve_existing_visible_identity_within_turn() -> None:
    # Given: a later streamed replacement carries a pre-disambiguated id.
    messages = (
        HumanMessage(id="user-1", content="First"),
        AIMessage(id="assistant-shared", content="Partial"),
        AIMessage(id="assistant-shared::moldy-turn-0", content="Final"),
    )

    # When
    sources = conversation_pinned_summaries._sources_for_messages(messages, "checkpoint-1")

    # Then: replacement content wins without changing the visible identity already rendered.
    assistant_sources = [source for source in sources if source.role == "assistant"]
    assert [(source.message_id, source.text) for source in assistant_sources] == [
        ("assistant-shared", "Final"),
    ]


def _current_user(user: User) -> CurrentUser:
    return CurrentUser(id=user.id, email=user.email, name=user.name)


def _isolated_app(
    db: AsyncSession,
    current_user: list[CurrentUser],
    *,
    bypass_csrf: bool = True,
) -> FastAPI:
    app = FastAPI()
    app.include_router(conversation_pinned_summaries.router)
    register_exception_handlers(app)

    async def override_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    async def override_user() -> CurrentUser:
        return current_user[0]

    async def override_csrf() -> None:
        return None

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    if bypass_csrf:
        app.dependency_overrides[verify_csrf] = override_csrf
    return app


class _CheckpointSource:
    def __init__(self, checkpoint_id: str, messages: list[Any]) -> None:
        self.checkpoint_id = checkpoint_id
        self.messages = messages

    async def alist(self, _config: Any) -> AsyncIterator[Any]:
        yield type(
            "CheckpointTuple",
            (),
            {
                "config": {"configurable": {"checkpoint_id": self.checkpoint_id}},
                "parent_config": None,
                "checkpoint": {"channel_values": {"messages": self.messages}},
            },
        )()


@pytest.mark.asyncio
async def test_pinned_summary_http_round_trip_survives_reload(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    user, _, agent = await seed_agent(db)
    conversation = Conversation(agent_id=agent.id)
    db.add(conversation)
    await db.flush()
    message_id = f"lc-run-{uuid.uuid4()}"
    conversation.active_branch_checkpoint_id = "checkpoint-1"
    checkpointer = _CheckpointSource(
        "checkpoint-1",
        [
            HumanMessage(id="user-1", content="Question"),
            AIMessage(id=message_id, content="Persisted answer"),
        ],
    )
    monkeypatch.setattr(conversation_pinned_summaries, "get_checkpointer", lambda: checkpointer)
    await db.commit()
    app = _isolated_app(db, [_current_user(user)], bypass_csrf=False)
    transport = ASGITransport(app=app)
    csrf = create_csrf_token(user.id)

    # When
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(settings.cookie_name_csrf, csrf)
        denied = await client.put(
            f"/api/conversations/{conversation.id}/pinned-summary",
            json={"message_id": message_id},
        )
        pinned = await client.put(
            f"/api/conversations/{conversation.id}/pinned-summary",
            json={"message_id": message_id},
            headers={"X-CSRF-Token": csrf},
        )
    async with TestSession() as reload_db:
        reload_app = _isolated_app(reload_db, [_current_user(user)], bypass_csrf=False)
        async with AsyncClient(
            transport=ASGITransport(app=reload_app),
            base_url="http://test",
        ) as reload_client:
            restored = await reload_client.get(
                f"/api/conversations/{conversation.id}/pinned-summary"
            )

    # Then
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "csrf_mismatch"
    assert pinned.status_code == 200
    assert restored.status_code == 200
    assert restored.json()["summary"]["source_message_id"] == message_id
    assert restored.json()["summary"]["snapshot_text"] == "Persisted answer"
    assert restored.json()["summary"]["source_branch_checkpoint_id"] == "checkpoint-1"
    assert restored.json()["summary"]["source_status"] == "current"


@pytest.mark.asyncio
async def test_pinned_summary_http_unpin_clears_selection(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    user, _, agent = await seed_agent(db)
    conversation = Conversation(agent_id=agent.id)
    db.add(conversation)
    await db.flush()
    message_id = f"lc-run-{uuid.uuid4()}"
    sources = (SummarySource(message_id, "assistant", "Answer", "checkpoint-1"),)

    async def load_sources(
        _conversation_id: uuid.UUID,
        _checkpoint_id: str | None,
    ) -> tuple[tuple[SummarySource, ...], tuple[SummarySource, ...]]:
        return sources, sources

    monkeypatch.setattr(conversation_pinned_summaries, "_load_sources", load_sources)
    app = _isolated_app(db, [_current_user(user)])
    transport = ASGITransport(app=app)

    # When
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            f"/api/conversations/{conversation.id}/pinned-summary",
            json={"message_id": message_id},
        )
        response = await client.delete(f"/api/conversations/{conversation.id}/pinned-summary")
        restored = await client.get(f"/api/conversations/{conversation.id}/pinned-summary")

    # Then
    assert response.status_code == 204
    assert restored.json() == {"summary": None}


@pytest.mark.asyncio
async def test_pinned_summary_http_hides_foreign_conversation(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    owner, _, owner_agent = await seed_agent(db)
    foreign_user_id = uuid.uuid4()
    foreign_user, _, _ = await seed_agent(db, user_id=foreign_user_id)
    conversation = Conversation(agent_id=owner_agent.id)
    db.add(conversation)
    await db.flush()
    message_id = f"lc-run-{uuid.uuid4()}"
    sources = (SummarySource(message_id, "assistant", "Private answer", "checkpoint-1"),)

    load_count = 0

    async def load_sources(
        _conversation_id: uuid.UUID,
        _checkpoint_id: str | None,
    ) -> tuple[tuple[SummarySource, ...], tuple[SummarySource, ...]]:
        nonlocal load_count
        load_count += 1
        return sources, sources

    monkeypatch.setattr(conversation_pinned_summaries, "_load_sources", load_sources)
    app = _isolated_app(db, [_current_user(foreign_user)])
    transport = ASGITransport(app=app)

    # When
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/api/conversations/{conversation.id}/pinned-summary",
            json={"message_id": message_id},
        )

    # Then
    assert owner.id == TEST_USER_ID
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert load_count == 0


@pytest.mark.asyncio
async def test_pinned_summary_http_rejects_malformed_message_identity(
    db: AsyncSession,
) -> None:
    # Given
    user, _, agent = await seed_agent(db)
    conversation = Conversation(agent_id=agent.id)
    db.add(conversation)
    await db.flush()
    app = _isolated_app(db, [_current_user(user)])
    transport = ASGITransport(app=app)

    # When
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            f"/api/conversations/{conversation.id}/pinned-summary",
            json={"message_id": "x" * 256},
        )

    # Then
    assert response.status_code == 422
