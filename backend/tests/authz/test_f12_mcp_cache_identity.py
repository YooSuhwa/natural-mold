"""F-12: MCP tool cache keys carry the credential identity; identity changes evict.

One user's cached tools (headers, injected ``auth_config`` arguments) never
serve another user, another binding or another credential; credential edits,
rotation, revocation, deletion and per-user binding changes drop the entries.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import mcp_cache, mcp_tool_cache
from app.agent_runtime.mcp_auth_context import McpAuthContext, mark_credential_auth_needed
from app.agent_runtime.mcp_tool_cache import identity_cache_key, invalidate_mcp_tool_cache
from app.models.credential import Credential
from app.models.resource_credential_binding import ResourceCredentialBinding
from tests.authz.f12_mock_mcp import mcp_config
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession


def _tool(name: str) -> StructuredTool:
    async def _call(**_: Any) -> str:
        return "ok"

    return StructuredTool.from_function(coroutine=_call, name=name, description=name)


@pytest.fixture
async def discoveries(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, servers: dict[str, Any], **kwargs: Any) -> None:
            seen.append({"servers": servers, **kwargs})

        async def get_tools(self) -> list[StructuredTool]:
            return [_tool("post_message")]

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient)
    await mcp_cache.clear_mcp_tool_cache()
    return seen


async def _build(**overrides: Any) -> None:
    from app.agent_runtime.mcp_tool_loader import _build_mcp_tools

    assert [t.name for t in await _build_mcp_tools([mcp_config(**overrides)])] == ["post_message"]


@pytest.mark.asyncio
async def test_identity_is_part_of_the_cache_key(discoveries) -> None:
    await _build()
    await _build()
    assert len(discoveries) == 1  # same identity: cache hit

    # Same URL and transport headers, other account / binding credential / tenant.
    await _build(credential_subject_user_id=str(uuid.uuid4()))
    await _build(credential_id=str(uuid.uuid4()))
    await _build(tenant_id=str(uuid.uuid4()), org_id=str(uuid.uuid4()))
    assert len(discoveries) == 4


@pytest.mark.asyncio
async def test_injected_auth_arguments_never_cross_users(discoveries) -> None:
    common = {"mcp_transport_headers": {}, "credentials": None, "credential_destination": None}
    await _build(auth_config={"api_key": "user-a"}, **common)
    await _build(auth_config={"api_key": "user-b"}, **common)

    assert len(discoveries) == 2
    injected = [d["tool_interceptors"][0].tool_auth for d in discoveries]
    assert injected == [
        {"post_message": {"api_key": "user-a"}},
        {"post_message": {"api_key": "user-b"}},
    ]


@pytest.mark.asyncio
async def test_invalidation_by_tag(discoveries) -> None:
    config = mcp_config()
    await _build()
    assert invalidate_mcp_tool_cache(credential_id=str(uuid.uuid4())) == 0
    assert invalidate_mcp_tool_cache() == 0
    assert invalidate_mcp_tool_cache(credential_id=config["credential_id"]) == 1
    await _build()
    assert len(discoveries) == 2
    assert (
        invalidate_mcp_tool_cache(
            server_id=config["mcp_server_id"], account_id=config["credential_subject_user_id"]
        )
        == 1
    )
    key, tags = identity_cache_key("srv", [config], {})
    assert key.startswith("srv|") and f"credential:{config['credential_id']}" in tags


@pytest.mark.asyncio
async def test_cache_is_lru_bounded_swept_and_prunes_locks(monkeypatch) -> None:
    await mcp_cache.clear_mcp_tool_cache()
    monkeypatch.setattr(mcp_tool_cache, "MAX_ENTRIES", 2)

    async def _factory() -> list[BaseTool]:
        return [_tool("t")]

    for key in ("a", "b"):
        await mcp_tool_cache.get_cached_mcp_tools(key, _factory, ttl_seconds=60)
    await mcp_tool_cache.get_cached_mcp_tools("a", _factory, ttl_seconds=60)  # a is recent
    await mcp_tool_cache.get_cached_mcp_tools("c", _factory, ttl_seconds=60)
    assert list(mcp_tool_cache._CACHE) == ["a", "c"]
    assert "b" not in mcp_tool_cache._LOCKS

    await mcp_cache.clear_mcp_tool_cache()
    clock = [1000.0]
    monkeypatch.setattr(mcp_tool_cache.time, "monotonic", lambda: clock[0])
    await mcp_tool_cache.get_cached_mcp_tools("d", _factory, ttl_seconds=5)
    clock[0] += 10
    await mcp_tool_cache.get_cached_mcp_tools("e", _factory, ttl_seconds=5)
    assert list(mcp_tool_cache._CACHE) == ["e"]  # expired entries swept on insert
    assert set(mcp_tool_cache._LOCKS) == {"e"}


async def _credential(db: AsyncSession) -> Credential:
    from tests.authz.f15_mock_as import ensure_user

    await ensure_user(db)
    row = Credential(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        definition_key="mcp_oauth2",
        name="MCP",
        data_encrypted="x",
        key_id="k1",
        status="active",
    )
    db.add(row)
    await db.commit()
    return row


@pytest.mark.asyncio
async def test_credential_and_binding_changes_evict(db: AsyncSession, discoveries) -> None:
    credential = await _credential(db)
    config = {"credential_id": str(credential.id), "mcp_server_id": str(uuid.uuid4())}
    config["credential_subject_user_id"] = str(TEST_USER_ID)

    await _build(**config)
    credential.last_used_at = None  # not an identity column: no eviction
    credential.name = "renamed"
    await db.commit()
    await _build(**config)
    assert len(discoveries) == 1

    credential.status = "revoked"  # revocation / auth_needed / rotation (data_encrypted)
    await db.commit()
    await _build(**config)
    assert len(discoveries) == 2

    binding = ResourceCredentialBinding(
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        user_id=TEST_USER_ID,
        resource_type="mcp_server",
        resource_id=uuid.UUID(config["mcp_server_id"]),
        credential_id=credential.id,
    )
    db.add(binding)
    await db.commit()
    await _build(**config)
    assert len(discoveries) == 3
    await db.delete(binding)
    await db.commit()
    await _build(**config)
    assert len(discoveries) == 4

    await db.delete(credential)
    await db.commit()
    assert not mcp_tool_cache._CACHE


@pytest.mark.asyncio
async def test_401_marks_credential_auth_needed(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, discoveries
) -> None:
    from app.agent_runtime import mcp_auth_context

    monkeypatch.setattr(mcp_auth_context, "async_session", TestSession)
    credential = await _credential(db)
    config = mcp_config(credential_id=str(credential.id), tenant_id=str(TEST_TENANT_ID))
    await _build(credential_id=config["credential_id"], tenant_id=config["tenant_id"])
    context = McpAuthContext.from_config(config)
    assert context is not None

    await mark_credential_auth_needed(context)

    await db.refresh(credential)
    assert credential.status == "auth_needed"
    assert not mcp_tool_cache._CACHE
