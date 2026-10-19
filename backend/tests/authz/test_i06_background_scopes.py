"""I-06: background work touches each company's rows in that company's session."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import scheduler
from app.database import (
    SCOPE_INFO_KEY,
    SessionTenant,
    TenantScoped,
    platform_sessions,
    session_tenant,
    tenant_sessions,
)
from app.mcp import discovery
from app.models.conversation import Conversation
from app.models.mcp_server import McpServer
from app.services import conversation_run_queue_worker as queue
from app.services import conversation_run_transitions as transitions
from app.services import mcp_health_poll
from app.services.conversation_run_task_registry import RunTaskRegistry
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


def _server(name: str, ctx: Any | None, *, owner: uuid.UUID | None = None) -> McpServer:
    if ctx is None:
        return McpServer(
            user_id=owner,
            name=name,
            transport="streamable_http",
            url="https://x.invalid",
            is_system=True,
        )
    return McpServer(
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        name=name,
        transport="streamable_http",
        url="https://x.invalid",
    )


async def test_mcp_health_poll_runs_each_company_in_its_own_session(
    db: AsyncSession, private_scopes: PrivateScopes, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = private_scopes.first, private_scopes.second
    db.add_all(
        [_server("a", first), _server("b", second), _server("catalog", None, owner=first.user_id)]
    )
    await db.commit()
    probed: dict[str, Any] = {}

    async def probe(session: AsyncSession, server: McpServer) -> dict[str, Any]:
        probed[server.name] = session.info.get(SCOPE_INFO_KEY)
        return {"success": True}

    monkeypatch.setattr(discovery, "test_server", probe)
    monkeypatch.setattr(scheduler, "async_session", TestSession)

    counters = await scheduler.poll_mcp_servers_health()

    assert counters == {"checked": 3, "ok": 3, "error": 0}
    assert probed == {
        "a": {"app.tenant_id": str(first.tenant_id)},
        "b": {"app.tenant_id": str(second.tenant_id)},
        "catalog": {"app.scope": "platform"},
    }
    rows = (await db.execute(select(McpServer.name, McpServer.health_status))).all()
    assert {row.name: row.health_status for row in rows} == {"a": "ok", "b": "ok", "catalog": "ok"}


async def test_failing_catalog_poll_does_not_skip_the_companies(
    db: AsyncSession, private_scopes: PrivateScopes, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = private_scopes.first
    db.add_all([_server("a", first), _server("catalog", None, owner=first.user_id)])
    await db.commit()

    async def probe(_session: AsyncSession, _server: McpServer) -> dict[str, Any]:
        return {"success": True}

    async def poll(**kwargs: Any) -> dict[str, int]:
        if kwargs["session_factory"] is platform:
            raise RuntimeError("catalog down")
        return await original(**kwargs)

    monkeypatch.setattr(discovery, "test_server", probe)
    original = mcp_health_poll.poll_mcp_servers_health
    platform = platform_sessions("scheduler.mcp_health", maker=TestSession)
    monkeypatch.setattr(mcp_health_poll, "poll_mcp_servers_health", poll)

    counters = await mcp_health_poll.poll_mcp_health_by_company(
        platform=platform,
        company_sessions=lambda tenant: tenant_sessions(SessionTenant(tenant), maker=TestSession),
    )

    assert counters == {"checked": 1, "ok": 1, "error": 0}


async def test_queue_rows_resolve_their_company_and_launch_in_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run, _agent, conversation = await seed_runtime_run()
    scope = await queue._company_of(  # noqa: SLF001 - the shared lookup itself
        select(Conversation.tenant_id, Conversation.user_id).where(
            Conversation.id == conversation.id
        )
    )
    assert scope == SessionTenant(TEST_TENANT_ID, TEST_USER_ID)
    assert await queue._input_company(uuid.uuid4()) is None  # noqa: SLF001

    opened: list[TenantScoped] = []

    def company_session(tenant: TenantScoped) -> AsyncSession:
        opened.append(tenant)
        return TestSession()

    async def company(_input_id: uuid.UUID) -> SessionTenant:
        return SessionTenant(TEST_TENANT_ID, TEST_USER_ID)

    monkeypatch.setattr(queue, "_company_session", company_session)
    monkeypatch.setattr(queue, "_input_company", company)
    assert await queue.launch_claimed_input(uuid.uuid4()) is None
    await queue._fail_claimed_input(uuid.uuid4(), error_code="x")  # noqa: SLF001
    assert opened == [SessionTenant(TEST_TENANT_ID, TEST_USER_ID)] * 2

    async def missing(_input_id: uuid.UUID) -> None:
        return None

    monkeypatch.setattr(queue, "_input_company", missing)
    assert await queue.launch_claimed_input(uuid.uuid4()) is None
    assert len(opened) == 2  # no session without a company


async def test_run_heartbeat_uses_the_run_company_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scopes: list[Any] = []

    @asynccontextmanager
    async def tenant_session(tenant: TenantScoped):  # type: ignore[no-untyped-def]
        async with TestSession() as session:
            session.info[SCOPE_INFO_KEY] = {"app.tenant_id": str(tenant.tenant_id)}
            scopes.append(session_tenant(session))
            yield session

    monkeypatch.setattr(transitions, "_tenant_session", tenant_session)
    monkeypatch.setattr(transitions, "_CANCEL_POLL_SECONDS", 0)
    tenant = SessionTenant(TEST_TENANT_ID)
    await transitions._heartbeat_until_terminal(uuid.uuid4(), RunTaskRegistry(), tenant)  # noqa: SLF001
    assert scopes == [TEST_TENANT_ID]
