"""F-15 test 5 (plan 11.4.1): ``403 insufficient_scope`` step-up, bounded to 2 retries.

The widened scope rides on the OAuth state and is applied only on success.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.mcp_oauth_step_up import (
    ScopeStepUpBudget,
    insufficient_scope_challenge,
    merge_scopes,
    parse_bearer_challenge,
    plan_scope_step_up,
)
from app.credentials.registry import registry
from tests.authz.f15_mock_as import (
    ISSUER,
    RESOURCE,
    MockAuthorizationServer,
    ensure_user,
    mcp_credential,
    start,
)

CHALLENGE = 'Bearer error="insufficient_scope", scope="write admin", resource_metadata="https://mcp.example/.well-known/oauth-protected-resource/mcp"'


def test_parse_challenge_and_merge() -> None:
    assert parse_bearer_challenge(CHALLENGE)["scope"] == "write admin"
    assert merge_scopes("read write", "write admin") == "read write admin"
    assert insufficient_scope_challenge(200, {"WWW-Authenticate": CHALLENGE}) is None
    assert insufficient_scope_challenge(403, {"www-authenticate": 'Bearer error="x"'}) is None


@pytest.mark.asyncio
async def test_insufficient_scope_reauthorizes_with_merged_scope(
    db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def mcp(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"WWW-Authenticate": CHALLENGE})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mcp)) as mcp_client:
        reply = await mcp_client.post(RESOURCE, json={"method": "tools/call"})
    challenge = insufficient_scope_challenge(reply.status_code, reply.headers)
    assert challenge is not None and challenge.scope == "write admin"

    await ensure_user(db)
    server = MockAuthorizationServer()
    server.install(monkeypatch)
    cred = await mcp_credential(db, scope="read")
    data = await credential_service.decrypt_with_external(cred)
    budget = ScopeStepUpBudget()
    plan = {
        "challenge": challenge,
        "budget": budget,
        "resource": RESOURCE,
        "resource_name": "MCP",
        "tool_name": "post_message",
        "credential_id": cred.id,
    }

    step_up = plan_scope_step_up(credential_data=data, **plan)
    assert step_up is not None
    assert step_up.scope == "read write admin"
    assert step_up.interrupt["type"] == "auth_required"
    assert step_up.interrupt["reason"] == "scope_upgrade"
    assert step_up.interrupt["items"][0]["credential_id"] == str(cred.id)

    async def stored_scope() -> str | None:
        await db.refresh(cred)
        return (await credential_service.decrypt_with_external(cred)).get("scope")

    # A failed re-authorization (iss mismatch) leaves the granted scope alone.
    _, params = await start(client, cred, scope=step_up.interrupt["items"][0]["connect"]["scope"])
    assert params["scope"] == "read write admin"
    assert params["resource"] == RESOURCE
    assert await stored_scope() == "read"
    bad = await client.get(
        "/api/oauth2-credential/callback",
        params={"code": "c", "state": params["state"], "iss": "https://evil.example"},
    )
    assert bad.status_code == 400
    assert await stored_scope() == "read"

    # Success applies the requested scope; refresh then keeps the granted one.
    _, params = await start(client, cred, scope=step_up.scope)
    ok = await client.get(
        "/api/oauth2-credential/callback",
        params={"code": "c", "state": params["state"], "iss": ISSUER},
    )
    assert ok.status_code == 200
    assert await stored_scope() == "read write admin"
    data = await credential_service.decrypt_with_external(cred)
    await registry.require("mcp_oauth2").pre_authentication(data)  # type: ignore[misc]
    assert server.token_requests[-1]["grant_type"] == "refresh_token"
    assert server.token_requests[-1]["scope"] == "read write admin"

    assert plan_scope_step_up(credential_data=data, **plan) is not None
    assert plan_scope_step_up(credential_data=data, **plan) is None  # third: budget spent
    other = {**plan, "tool_name": "read_channel", "credential_id": uuid.uuid4()}
    assert plan_scope_step_up(credential_data=data, **other) is not None
