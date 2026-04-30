"""Tests for the greenfield Tools domain (M3).

Covers:
- Catalog endpoint surfaces every registered :class:`ToolDefinition`.
- CRUD round-trips, parameter validation, per-user isolation.
- ``http_request`` runner via :class:`httpx.MockTransport`.
- Naver search runner uses a credential's GenericAuth recipe.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.tool import Tool
from app.models.user import User
from app.tools import registry as tool_registry_mod
from app.tools.domain import ToolDefinition, ToolRunContext
from app.tools.parameters import FieldDef, FieldKind
from app.tools.registry import registry as tool_registry
from app.tools.runner import run_tool
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


# -- Catalog -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_types_catalog(client: AsyncClient) -> None:
    response = await client.get("/api/tool-types")
    assert response.status_code == 200
    keys = {item["key"] for item in response.json()}
    assert {
        "http_request",
        "naver_search_blog",
        "naver_search_news",
        "naver_search_image",
        "naver_search_shop",
        "naver_search_local",
        "google_search_web",
        "google_search_image",
        "google_search_news",
        "gmail_send",
        "google_calendar_event",
        "google_chat_message",
    } <= keys


@pytest.mark.asyncio
async def test_tool_type_detail_carries_parameters(client: AsyncClient) -> None:
    response = await client.get("/api/tool-types/http_request")
    assert response.status_code == 200
    body = response.json()
    names = [p["name"] for p in body["parameters"]]
    assert "method" in names and "url" in names
    assert "http_bearer" in body["credential_definition_keys"]


@pytest.mark.asyncio
async def test_tool_type_unknown(client: AsyncClient) -> None:
    response = await client.get("/api/tool-types/no-such-tool")
    assert response.status_code == 404


# -- CRUD --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tool_round_trip(client: AsyncClient, db: AsyncSession) -> None:
    response = await client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "Ping",
            "parameters": {
                "method": "GET",
                "url": "https://example.com",
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    tool_id = uuid.UUID(body["id"])
    assert body["definition_key"] == "http_request"
    assert body["enabled"] is True

    row = (
        await db.execute(select(Tool).where(Tool.id == tool_id))
    ).scalar_one()
    assert row.parameters["url"] == "https://example.com"
    assert row.user_id == TEST_USER_ID


@pytest.mark.asyncio
async def test_create_tool_unknown_definition(client: AsyncClient) -> None:
    response = await client.post(
        "/api/tools",
        json={
            "definition_key": "no_such_tool",
            "name": "x",
            "parameters": {},
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_tool_missing_required_parameter(client: AsyncClient) -> None:
    response = await client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "Bad",
            "parameters": {"method": "GET"},  # missing 'url'
        },
    )
    assert response.status_code == 422
    assert "url" in response.text


@pytest.mark.asyncio
async def test_list_tools_filters(client: AsyncClient) -> None:
    await client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "A",
            "parameters": {"method": "GET", "url": "https://a"},
            "enabled": True,
        },
    )
    await client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "B",
            "parameters": {"method": "GET", "url": "https://b"},
            "enabled": False,
        },
    )
    listing = await client.get("/api/tools?enabled=true")
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 1
    assert items[0]["name"] == "A"


@pytest.mark.asyncio
async def test_patch_tool(client: AsyncClient) -> None:
    create = await client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "Old",
            "parameters": {"method": "GET", "url": "https://x"},
        },
    )
    tool_id = create.json()["id"]
    patch = await client.patch(
        f"/api/tools/{tool_id}",
        json={"name": "Renamed", "enabled": False},
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["name"] == "Renamed"
    assert body["enabled"] is False


@pytest.mark.asyncio
async def test_delete_tool(client: AsyncClient, db: AsyncSession) -> None:
    create = await client.post(
        "/api/tools",
        json={
            "definition_key": "http_request",
            "name": "Bye",
            "parameters": {"method": "GET", "url": "https://x"},
        },
    )
    tool_id = uuid.UUID(create.json()["id"])
    response = await client.delete(f"/api/tools/{tool_id}")
    assert response.status_code == 204
    row = (
        await db.execute(select(Tool).where(Tool.id == tool_id))
    ).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_other_user_cannot_access(client: AsyncClient, db: AsyncSession) -> None:
    other_id = uuid.uuid4()
    db.add(User(id=other_id, email="other@test.com", name="Other"))
    await db.commit()
    db.add(
        Tool(
            user_id=other_id,
            definition_key="http_request",
            name="Other's Tool",
            parameters={"method": "GET", "url": "https://x"},
        )
    )
    await db.commit()
    listing = await client.get("/api/tools")
    names = [t["name"] for t in listing.json()]
    assert "Other's Tool" not in names


# -- Runner: HTTP Request ----------------------------------------------------


@pytest.mark.asyncio
async def test_run_http_request_with_mock_transport(
    db: AsyncSession,
) -> None:
    """The http_request runner issues a real HTTP call through MockTransport."""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"ok": True}).encode(),
        )

    transport = httpx.MockTransport(handler)
    tool = Tool(
        user_id=TEST_USER_ID,
        definition_key="http_request",
        name="Test",
        parameters={
            "method": "POST",
            "url": "https://api.example.com/v1/echo",
            "json_body": {"hello": "world"},
        },
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_tool(
            db=db,
            tool=tool,
            registry=tool_registry,
            http_client=client,
        )

    assert result.success, result.error
    assert result.http_status == 200
    assert result.result["body"] == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.example.com/v1/echo"


@pytest.mark.asyncio
async def test_run_http_request_with_credential(db: AsyncSession) -> None:
    """When a Bearer credential is bound, the runner injects the Authorization header."""

    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="http_bearer",
        name="bearer",
        data={"token": "supersecret"},
    )
    await db.commit()

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    tool = Tool(
        user_id=TEST_USER_ID,
        definition_key="http_request",
        name="With Auth",
        parameters={
            "method": "GET",
            "url": "https://api.example.com/v1/secret",
            "_credential_definition_key": "http_bearer",
        },
        credential_id=cred.id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_tool(
            db=db,
            tool=tool,
            registry=tool_registry,
            http_client=client,
        )

    assert result.success, result.error
    assert captured["auth"] == "Bearer supersecret"


# -- Runner: Naver via GenericAuth -------------------------------------------


@pytest.mark.asyncio
async def test_run_naver_search_uses_credential_headers(db: AsyncSession) -> None:
    cred = await credential_service.create(
        db,
        user_id=TEST_USER_ID,
        definition_key="naver_search",
        name="naver",
        data={"client_id": "id-1", "client_secret": "sec-2"},
    )
    await db.commit()

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["client_id"] = request.headers.get("X-Naver-Client-Id")
        captured["client_secret"] = request.headers.get("X-Naver-Client-Secret")
        return httpx.Response(
            200,
            json={"total": 0, "items": []},
        )

    tool = Tool(
        user_id=TEST_USER_ID,
        definition_key="naver_search_blog",
        name="Blog",
        parameters={"query": "fastapi"},
        credential_id=cred.id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_tool(
            db=db, tool=tool, registry=tool_registry, http_client=client
        )

    assert result.success, result.error
    assert "openapi.naver.com/v1/search/blog.json" in captured["url"]
    assert captured["client_id"] == "id-1"
    assert captured["client_secret"] == "sec-2"


# -- Runner: validation / error envelope -------------------------------------


@pytest.mark.asyncio
async def test_run_tool_returns_error_envelope_on_missing_credential(
    db: AsyncSession,
) -> None:
    tool = Tool(
        user_id=TEST_USER_ID,
        definition_key="naver_search_blog",
        name="No Cred",
        parameters={"query": "x"},
        credential_id=None,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    result = await run_tool(db=db, tool=tool, registry=tool_registry)
    assert result.success is False
    assert "credential" in result.error.lower()


@pytest.mark.asyncio
async def test_registry_is_singleton() -> None:
    """``app.tools.registry`` must expose the populated singleton."""

    assert tool_registry_mod is not None
    assert tool_registry.get("http_request") is not None


def test_register_local_definition_round_trip() -> None:
    """A test-only registry round trip — no global pollution."""

    from app.tools.registry import ToolRegistry

    async def _runner(_ctx: ToolRunContext) -> str:
        return "hi"

    local = ToolRegistry()
    definition = ToolDefinition(
        key="local_only",
        display_name="Local",
        description="",
        parameters=[FieldDef(name="x", display_name="X", kind=FieldKind.STRING)],
        runner=_runner,
    )
    local.register(definition)
    assert local.get("local_only") is definition
    assert "local_only" in [d.key for d in local.all()]
