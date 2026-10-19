from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, assert_never

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service
from app.agent_api import service as agent_api_service
from app.agent_api.dependencies import ApiKeyPrincipal
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.api_key_actor import resolve_api_key_actor
from app.authz.org_context import OrgContext
from app.dependencies import get_db
from app.main import create_app
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.conversation_run import ConversationRun
from app.models.user import User
from app.schemas.agent_api import AgentApiKeyCreate
from app.services import agent_execution_runtime
from tests.authz.agent_lifecycle_fixture import RegisteredLifecycleFixture

type NativeSurface = Literal[
    "wait",
    "native_stream",
    "openai_stream",
    "dify_blocking",
    "thread_wait",
]


@dataclass(frozen=True, slots=True)
class NativeApiHarness:
    fixture: RegisteredLifecycleFixture
    app: FastAPI
    deployment: AgentDeployment
    principal: ApiKeyPrincipal
    cleartext_key: str
    owner_email: str


@dataclass(frozen=True, slots=True)
class NativeSurfaceRequest:
    client: AsyncClient
    harness: NativeApiHarness
    surface: NativeSurface


@asynccontextmanager
async def native_api_harness(
    fixture: RegisteredLifecycleFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[NativeApiHarness]:
    context = OrgContext(
        user_id=fixture.actors.owner_one_id,
        org_id=fixture.actors.org_id,
        tenant_id=fixture.actors.tenant_id,
    )
    async with fixture.sessions() as db:
        await db.execute(
            update(Agent)
            .where(Agent.id.in_(fixture.graph.child_agent_ids))
            .values(user_id=fixture.actors.owner_one_id)
        )
        await db.commit()
        deployment = await db.scalar(
            select(AgentDeployment).where(AgentDeployment.agent_id == fixture.graph.root_agent_id)
        )
        owner = await db.get(User, fixture.actors.owner_one_id)
        assert deployment is not None and owner is not None
        key, cleartext = await agent_api_service.create_api_key(
            db,
            context,
            AgentApiKeyCreate(
                name="Task10 native key",
                scopes=["invoke", "stream", "read"],
                deployment_ids=[deployment.id],
            ),
        )
        principal = ApiKeyPrincipal(
            key=key,
            user_id=key.user_id,
            context=await resolve_api_key_actor(db, key),
        )
        owner_email = owner.email

    async def build_native_config(_db, agent, **kwargs) -> AgentConfig:
        return AgentConfig(
            provider="openai_compatible",
            model_name="fixture-model",
            api_key=None,
            base_url="http://127.0.0.1.invalid",
            system_prompt=agent.system_prompt,
            tools_config=[],
            thread_id=kwargs["thread_id"],
            org_context=kwargs["context"],
            runtime_policy=kwargs["runtime_policy"],
            agent_id=str(agent.id),
            user_id=str(kwargs["context"].user_id),
        )

    monkeypatch.setattr(agent_execution_runtime, "async_session", fixture.sessions)
    monkeypatch.setattr(runtime_service, "build_agent_config_for_loaded_agent", build_native_config)
    app = create_app()

    async def database_session() -> AsyncGenerator[AsyncSession, None]:
        async with fixture.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database_session
    yield NativeApiHarness(fixture, app, deployment, principal, cleartext, owner_email)


async def archive_root(harness: NativeApiHarness) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=harness.app), base_url="http://fixture"
    ) as client:
        login = await client.post(
            "/api/auth/login",
            json={"email": harness.owner_email, "password": "fixture password 42"},
        )
        assert login.status_code == 200, login.text
        archived = await client.delete(
            f"/api/agents/{harness.fixture.graph.root_agent_id}",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
        assert archived.status_code == 204, archived.text


async def load_api_run(
    fixture: RegisteredLifecycleFixture,
    mode: str,
) -> tuple[AgentApiRun, ConversationRun]:
    async with fixture.sessions() as db:
        envelope = await db.scalar(
            select(AgentApiRun)
            .where(AgentApiRun.mode == mode)
            .order_by(AgentApiRun.created_at.desc())
        )
        assert envelope is not None and envelope.conversation_run_id is not None
        run = await db.get(ConversationRun, envelope.conversation_run_id)
        assert run is not None
        return envelope, run


async def invoke_surface(request: NativeSurfaceRequest) -> Response:
    harness = request.harness
    headers = {"Authorization": f"Bearer {harness.cleartext_key}"}
    message_input = {"messages": [{"role": "user", "content": "native"}]}
    match request.surface:
        case "wait":
            return await request.client.post(
                "/v1/runs/wait",
                headers=headers,
                json={"agent_id": harness.deployment.public_id, "input": message_input},
            )
        case "native_stream":
            return await request.client.post(
                "/v1/runs/stream",
                headers=headers,
                json={"agent_id": harness.deployment.public_id, "input": message_input},
            )
        case "openai_stream":
            return await request.client.post(
                "/v1/chat/completions",
                headers=headers,
                json={
                    "model": harness.deployment.public_id,
                    "messages": message_input["messages"],
                    "stream": True,
                },
            )
        case "dify_blocking":
            return await request.client.post(
                f"/v1/agents/{harness.deployment.public_id}/chat-messages",
                headers=headers,
                json={"query": "native", "response_mode": "blocking"},
            )
        case "thread_wait":
            thread = await request.client.post(
                "/v1/threads",
                headers=headers,
                json={"agent_id": harness.deployment.public_id},
            )
            assert thread.status_code == 201, thread.text
            return await request.client.post(
                f"/v1/threads/{thread.json()['id']}/runs/wait",
                headers=headers,
                json={"agent_id": harness.deployment.public_id, "input": message_input},
            )
        case _:
            assert_never(request.surface)
