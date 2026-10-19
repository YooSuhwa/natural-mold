"""D-01: conversation-family route policies and agent re-checks (plan 9.3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy import select

from app.authz import runtime
from app.authz.policies import RES, policy_kind, policy_of
from app.authz.runtime import get_authz
from app.authz.service import AuthzService, ShadowSample, Verdict
from app.config import settings
from app.main import create_app
from app.models.conversation import Conversation
from tests.authz.authz_service_testkit import RecordingClient
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_conversations_router import _seed_agent
from tests.test_trace_debug_api import _seed_conversation, _seed_trace

DOMAIN_MODULES = {
    f"app.routers.{name}"
    for name in [
        "artifacts",
        "conversation_ag_ui",
        "conversation_agent_protocol",
        "conversation_agent_protocol_sdk",
        "conversation_branches",
        "conversation_crud",
        "conversation_files",
        "conversation_followup",
        "conversation_messages",
        "conversation_pinned_summaries",
        "conversation_run_inputs",
        "conversation_runs",
        "conversation_side_chats",
        "conversation_traces",
        "feedback",
        "mcp_apps",
        "memory",
        "shares",
        "uploads",
    ]
}
SHARE_TOKEN_ROUTES = {
    "/api/shares/{share_token}",
    "/api/shares/{share_token}/messages",
    "/api/shares/{share_token}/artifacts",
    "/api/shares/{share_token}/artifacts/{artifact_id}",
    "/api/shares/{share_token}/artifacts/{artifact_id}/content",
    "/api/shares/{share_token}/artifacts/{artifact_id}/download",
}
RES_ROUTES = {"/api/agents/{agent_id}/memory-settings"}
USER = f"user:{TEST_USER_ID}"


def _expected(path: str) -> str:
    if path in SHARE_TOKEN_ROUTES:
        return "SHARE_TOKEN"
    return "RES" if path in RES_ROUTES else "PRIVATE"


def test_conversation_family_routes_declare_their_plan_policy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "e2e_test_helpers_enabled", True)
    app = create_app()
    routes = [
        (method, route.path, policy_kind(route.endpoint))
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ in DOMAIN_MODULES
        for method in route.methods
    ]
    assert len(routes) == 89
    assert [r for r in routes if r[2] != _expected(r[1])] == []
    memory = next(r for r in app.routes if isinstance(r, APIRoute) and r.path in RES_ROUTES)
    assert policy_of(memory.endpoint) == RES(
        "agent", "can_edit", param="agent_id", min_visible="can_run"
    )


@pytest.fixture
def install(test_app: FastAPI, monkeypatch) -> Iterator:
    """Swap the process service for both route (get_authz) and service-level checks."""

    def _install(service: AuthzService) -> AuthzService:
        test_app.dependency_overrides[get_authz] = lambda: service
        monkeypatch.setattr(runtime, "_current", service)
        return service

    yield _install
    test_app.dependency_overrides.pop(get_authz, None)


def _service(
    mode: str,
    allowed: Iterable[tuple[str, str, str]] = (),
    *,
    unavailable: bool = False,
    samples: list[ShadowSample] | None = None,
) -> AuthzService:
    hook = samples.append if samples is not None else None
    client = RecordingClient(allowed=frozenset(allowed), unavailable=unavailable)
    return AuthzService(client, mode=mode, shadow_hook=hook)  # type: ignore[arg-type]


def _code(response) -> str:
    return response.json()["error"]["code"]


@pytest.mark.asyncio
async def test_create_conversation_enforces_agent_can_run(client: AsyncClient, install) -> None:
    agent_id, _ = await _seed_agent()
    obj = f"agent:{agent_id}"
    install(_service("enforce"))
    denied = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "x"})
    assert denied.status_code == 404 and _code(denied) == "AGENT_NOT_FOUND"
    install(_service("enforce", {(USER, "can_run", obj)}))
    allowed = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "x"})
    assert allowed.status_code == 201
    listed = await client.get(f"/api/agents/{agent_id}/conversations")
    assert [row["title"] for row in listed.json()] == ["x"]  # the denied create rolled back


@pytest.mark.asyncio
async def test_create_conversation_fails_closed_when_openfga_is_down(
    client: AsyncClient, install
) -> None:
    agent_id, _ = await _seed_agent()
    install(_service("enforce", unavailable=True))
    response = await client.post(f"/api/agents/{agent_id}/conversations/draft")
    assert response.status_code == 503 and _code(response) == "authz_unavailable"


@pytest.mark.asyncio
async def test_shadow_answers_legacy_and_records_the_run_sample(
    client: AsyncClient, install
) -> None:
    agent_id, _ = await _seed_agent()
    samples: list[ShadowSample] = []
    install(_service("shadow", samples=samples))  # OpenFGA would deny everything
    response = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "s"})
    assert response.status_code == 201
    assert [(s.permission, s.obj, s.legacy) for s in samples] == [
        ("can_run", f"agent:{agent_id}", Verdict.ALLOW)
    ]


@pytest.mark.asyncio
async def test_legacy_mode_records_nothing(client: AsyncClient, install) -> None:
    agent_id, _ = await _seed_agent()
    samples: list[ShadowSample] = []
    install(_service("legacy", samples=samples))
    response = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "l"})
    assert response.status_code == 201 and samples == []


@pytest.mark.asyncio
async def test_debug_traces_require_agent_view_config(client: AsyncClient, install) -> None:
    conversation_id = await _seed_conversation()
    await _seed_trace(conversation_id)
    url = f"/api/conversations/{conversation_id}/debug/traces"
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        agent_obj = f"agent:{conversation.agent_id}"
    install(_service("enforce"))
    hidden = await client.get(url)
    assert hidden.status_code == 404 and _code(hidden) == "AGENT_NOT_FOUND"
    install(_service("enforce", {(USER, "can_run", agent_obj)}))
    forbidden = await client.get(url)
    assert forbidden.status_code == 403 and _code(forbidden) == "permission_required"
    assert forbidden.json()["error"]["details"]["required"] == "can_view_config"
    install(
        _service("enforce", {(USER, "can_run", agent_obj), (USER, "can_view_config", agent_obj)})
    )
    assert (await client.get(url)).status_code == 200
    assert (await client.get(f"/api/conversations/{conversation_id}/traces")).status_code == 200


@pytest.mark.asyncio
async def test_agent_memory_settings_require_can_edit(client: AsyncClient, install) -> None:
    agent_id, _ = await _seed_agent()
    obj, url = f"agent:{agent_id}", f"/api/agents/{agent_id}/memory-settings"
    install(_service("enforce", {(USER, "can_run", obj)}))
    forbidden = await client.patch(url, json={"memory_policy_override": "auto"})
    assert forbidden.status_code == 403 and _code(forbidden) == "permission_required"
    install(_service("enforce", {(USER, "can_run", obj), (USER, "can_edit", obj)}))
    current = await client.get(url)
    assert current.status_code == 200
    assert current.json()["memory_policy_override"] == "inherit"  # denied PATCH never wrote


@pytest.mark.asyncio
async def test_agent_memory_settings_shadow_legacy_verdict_hides_foreign_agent(
    client: AsyncClient, install
) -> None:
    agent_id, _ = await _seed_agent(user_id=uuid.uuid4(), user_email="other@test.com")
    samples: list[ShadowSample] = []
    install(_service("shadow", samples=samples))
    response = await client.get(f"/api/agents/{agent_id}/memory-settings")
    assert response.status_code == 404 and _code(response) == "AGENT_NOT_FOUND"
    assert [(s.permission, s.legacy) for s in samples] == [("can_edit", Verdict.NOT_FOUND)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("post", "messages", {"content": "hi"}),
        ("post", "side-chats", None),
        ("post", "run-inputs/resume", None),
        ("post", "messages/regenerate", {}),
    ],
)
async def test_run_continuations_recheck_agent_can_run(
    client: AsyncClient, install, method: str, suffix: str, body
) -> None:
    agent_id, _ = await _seed_agent()
    created = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "c"})
    conversation_id = created.json()["id"]
    install(_service("enforce"))
    url = f"/api/conversations/{conversation_id}/{suffix}"
    response = await getattr(client, method)(url, json=body)
    assert response.status_code == 404 and _code(response) == "AGENT_NOT_FOUND"


async def _conversation_count(agent_id: uuid.UUID) -> int:
    async with TestSession() as db:
        rows = await db.scalars(select(Conversation.id).where(Conversation.agent_id == agent_id))
        return len(list(rows))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("conversations", {"title": "x"}),
        ("conversations/draft", None),
        ("conversations/start", {"content": "hi"}),
    ],
)
async def test_denied_create_never_writes_a_conversation(
    client: AsyncClient, install, suffix: str, body
) -> None:
    agent_id, _ = await _seed_agent()
    install(_service("enforce"))
    response = await client.post(f"/api/agents/{agent_id}/{suffix}", json=body)
    assert response.status_code == 404 and _code(response) == "AGENT_NOT_FOUND"
    assert await _conversation_count(agent_id) == 0


@pytest.mark.asyncio
async def test_denied_side_chat_never_writes_a_conversation(client: AsyncClient, install) -> None:
    agent_id, _ = await _seed_agent()
    created = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "p"})
    install(_service("enforce"))
    response = await client.post(f"/api/conversations/{created.json()['id']}/side-chats")
    assert response.status_code == 404 and _code(response) == "AGENT_NOT_FOUND"
    assert await _conversation_count(agent_id) == 1


@pytest.mark.asyncio
async def test_route_override_reaches_service_level_run_check(
    client: AsyncClient, test_app: FastAPI
) -> None:
    """Only ``get_authz`` is overridden: prepare_executable_conversation must still use it."""
    agent_id, _ = await _seed_agent()
    created = await client.post(f"/api/agents/{agent_id}/conversations", json={"title": "o"})
    test_app.dependency_overrides[get_authz] = lambda: _service("enforce")
    try:
        response = await client.post(
            f"/api/conversations/{created.json()['id']}/messages", json={"content": "hi"}
        )
    finally:
        test_app.dependency_overrides.pop(get_authz, None)
    assert response.status_code == 404 and _code(response) == "AGENT_NOT_FOUND"
