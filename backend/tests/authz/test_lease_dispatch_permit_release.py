"""The dispatch permit releases its authority transaction before the leaf I/O.

lock -> decide -> record -> release -> I/O: while a provider send or tool call
is parked inside its permit, the permit holds zero sessions/connections, so an
archive or revocation commits without waiting; the in-flight leaf completes and
the very next dispatch boundary observes the committed change.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_core.messages import ToolMessage
from sqlalchemy import update

from app.agent_runtime.lease_bound_model import LeaseSendGuard
from app.agent_runtime.lease_bound_tool import LeaseBoundToolMiddleware
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError, open_dispatch_permit
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.agent_runtime.runtime_effective_capabilities import build_effective_census
from app.agent_runtime.tool_access_policy import ToolOrigin
from app.authz.org_context import OrgContext
from app.models.organization import Organization, OrganizationMember
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.authz.prepared_adapter_testkit import CountingSessions
from tests.conftest import TestSession

PROVIDER = "provider:root"
WRITER_SECONDS = 2.0
CENSUS = build_effective_census(
    "root",
    effective_tool_names=["web_search"],
    selected_tools=(ToolOrigin("web_search", "builtin", "builtin:web_search"),),
    middleware=[],
    backends={},
    providers=(),
)
ALLOWED = CENSUS.descriptor_ids | {PROVIDER}


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    reset_prepared_run_registry_for_tests()


async def _lease(graph: GatewayGraph):
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    ctx = graph.context
    binding = PreparedRunBinding(
        ctx.tenant_id,
        ctx.org_id,
        ctx.user_id,
        graph.agent_id,
        graph.conversation_ids[0],
        uuid.uuid4(),
        "thread",
    )
    token = registry.reserve(binding)
    lease = replace(
        kit.lease(binding, token), descriptor_ids=ALLOWED, tool_access=CENSUS.tool_access()
    )
    await registry.stage(lease)
    registry.activate(binding.run_id, token=token, digest=lease.digest)
    return lease


async def _commit_writer(ctx: OrgContext, *, revoke: bool) -> None:
    """Archive (and optionally revoke) from another session; must not wait on the permit."""

    async def write() -> None:
        async with TestSession() as tx:
            stamp = datetime.now(UTC)
            org = update(Organization).where(Organization.id == ctx.org_id)
            await tx.execute(org.values(status="archived", archived_at=stamp))
            if revoke:
                member = update(OrganizationMember).where(
                    OrganizationMember.org_id == ctx.org_id,
                    OrganizationMember.user_id == ctx.user_id,
                )
                await tx.execute(member.values(status="suspended"))
            await tx.commit()

    await asyncio.wait_for(write(), WRITER_SECONDS)


async def _next_boundary(lease: Any, sessions: CountingSessions) -> Any:
    async with open_dispatch_permit(
        lease,
        kind="provider",
        descriptor_id=PROVIDER,
        allowed_descriptors=ALLOWED,
        session_factory=sessions,
    ) as permit:
        return permit


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", [False, True])
async def test_provider_leaf_holds_no_session_and_writer_wins_next_boundary(
    db: Any, revoke: bool
) -> None:
    graph = await seed_graph(db)
    lease = await _lease(graph)
    sessions = CountingSessions(TestSession)
    guard = LeaseSendGuard(lease, PROVIDER, ALLOWED, cast(Any, sessions))
    seen: dict[str, Any] = {}

    async def attempt() -> str:
        seen["open"] = sessions.open
        await _commit_writer(graph.context, revoke=revoke)
        return "sent"

    assert await guard.send(attempt) == "sent"  # in-flight leaf completes
    assert seen["open"] == 0 and sessions.open == 0
    if revoke:
        with pytest.raises(DispatchDeniedError) as denied:
            await _next_boundary(lease, sessions)
        assert denied.value.code == "forbidden"
    else:
        assert (await _next_boundary(lease, sessions)).archived_bypass is True


@pytest.mark.asyncio
async def test_tool_leaf_holds_no_session_and_revocation_denies_next_tool(db: Any) -> None:
    graph = await seed_graph(db)
    lease = await _lease(graph)
    sessions = CountingSessions(TestSession)
    guard = LeaseBoundToolMiddleware(lease, CENSUS, session_factory=cast(Any, sessions))
    entered, release, seen = asyncio.Event(), asyncio.Event(), []

    async def handler(request: Any) -> ToolMessage:
        seen.append(sessions.open)
        entered.set()
        await release.wait()
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

    def request(call_id: str) -> Any:
        call = {"name": "web_search", "args": {"query": "q"}, "id": call_id, "type": "tool_call"}
        return SimpleNamespace(tool_call=call)

    leaf = asyncio.create_task(guard.awrap_tool_call(request("call-1"), handler))
    await asyncio.wait_for(entered.wait(), WRITER_SECONDS)
    await _commit_writer(graph.context, revoke=True)  # commits while the leaf is parked
    release.set()
    first = await leaf
    second = await guard.awrap_tool_call(request("call-2"), handler)

    assert isinstance(first, ToolMessage) and first.content == "ok" and seen == [0]
    assert isinstance(second, ToolMessage) and second.status == "error"
    assert [d.code for d in guard.denials] == ["forbidden"]
    assert sessions.open == 0
