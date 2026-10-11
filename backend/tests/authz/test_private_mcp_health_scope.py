"""MCP health names, history and probes stay within the selected company."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential import Credential
from app.models.health_check_history import HealthCheckHistory
from app.models.mcp_server import McpServer
from app.services import health_check

if TYPE_CHECKING:
    from tests.authz.test_private_mcp_scope import McpCase

pytest_plugins = ["tests.authz.test_private_mcp_scope"]


@dataclass(frozen=True, slots=True)
class HealthCase:
    mcp: McpCase
    history: HealthCheckHistory


@pytest.fixture
async def health_case(db: AsyncSession, mcp_case: McpCase) -> HealthCase:
    history = HealthCheckHistory(
        target_kind="mcp_server",
        target_id=mcp_case.server_id,
        status="healthy",
        error_message="Private company history",
    )
    db.add(history)
    await db.commit()
    return HealthCase(mcp_case, history)


@pytest.mark.parametrize("operation", ["list", "history", "probe"])
async def test_mcp_health_is_hidden_after_company_switch(
    health_case: HealthCase,
    operation: Literal["list", "history", "probe"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = health_case.mcp
    client, org = case.client, case.organization

    async def successful_probe(
        session: AsyncSession, *, server: McpServer, credential: Credential | None
    ) -> HealthCheckHistory:
        assert server.id == case.server_id and credential is None
        row = await session.get(HealthCheckHistory, health_case.history.id)
        assert row is not None
        return row

    probe = AsyncMock(side_effect=successful_probe)
    monkeypatch.setattr(health_check, "check_mcp_server", probe)
    params = {"target_kind": "mcp_server", "target_id": str(case.server_id)}
    own = await client.get("/api/health/history", params=params)
    assert own.status_code == 200 and len(own.json()) == 1
    own_probe = await client.post("/api/health/check", params=params, headers=org.session.headers())
    assert own_probe.status_code == 200
    probe.assert_awaited_once()
    probe.reset_mock()
    switched = await client.post(
        "/api/me/active-org", json={"org_id": str(org.second)}, headers=org.session.headers()
    )
    assert switched.status_code == 200
    match operation:
        case "list":
            response = await client.get("/api/health/mcp-servers")
            assert response.status_code == 200 and response.json() == []
        case "history":
            response = await client.get("/api/health/history", params=params)
            assert response.status_code == 404
        case "probe":
            response = await client.post(
                "/api/health/check", params=params, headers=org.session.headers()
            )
            assert response.status_code == 404
        case unreachable:
            assert_never(unreachable)
    probe.assert_not_awaited()
