"""Every leaf dispatch re-proves authority; only the exact lease bypasses archive."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import (
    DispatchDeniedError,
    PermitKind,
    open_dispatch_permit,
)
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.runtime_agent_preparation import _prepare_runtime_components
from app.agent_runtime.runtime_config import AgentConfig, PreparedRunBinding
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.conftest import TestSession

DESCRIPTORS = frozenset({"tool:search", "provider:root"})


async def _active_lease(graph: GatewayGraph, *, independent_job: bool = False):
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
        independent_job=independent_job,
    )
    token = registry.reserve(binding)
    lease = kit.lease(binding, token)
    await registry.stage(lease)
    registry.activate(binding.run_id, token=token, digest=lease.digest)
    return registry, lease


async def _permit(lease, descriptor: str = "tool:search"):
    async with open_dispatch_permit(
        lease,
        kind="tool",
        descriptor_id=descriptor,
        allowed_descriptors=DESCRIPTORS,
        session_factory=TestSession,
    ) as permit:
        return permit


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    reset_prepared_run_registry_for_tests()


@pytest.mark.asyncio
async def test_active_lease_gets_a_permit_and_archive_is_bypassed_only_by_it(
    db: AsyncSession,
) -> None:
    graph = await seed_graph(db)
    _registry, lease = await _active_lease(graph)
    assert (await _permit(lease)).archived_bypass is False

    organization = await db.get(Organization, graph.context.org_id)
    assert organization is not None
    organization.status, organization.archived_at = "archived", datetime.now(UTC)
    await db.commit()

    assert (await _permit(lease)).archived_bypass is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("tenant_suspended", "tenant_suspended"),
        ("company_member_removed", "forbidden"),
        ("org_member_suspended", "forbidden"),
        ("org_member_removed_while_archived", "forbidden"),
    ],
)
async def test_suspension_and_either_membership_revocation_deny(
    db: AsyncSession, case: str, code: str
) -> None:
    graph = await seed_graph(db)
    _registry, lease = await _active_lease(graph)
    ctx = graph.context
    if case == "tenant_suspended":
        tenant = await db.get(Tenant, ctx.tenant_id)
        assert tenant is not None
        tenant.status = "suspended"
    elif case == "company_member_removed":
        member = await db.get(TenantMember, (ctx.tenant_id, ctx.user_id))
        assert member is not None
        member.status = "removed"
    else:
        member = await db.get(OrganizationMember, (ctx.org_id, ctx.user_id))
        assert member is not None
        member.status = "removed" if "removed" in case else "suspended"
        if "archived" in case:
            org = await db.get(Organization, ctx.org_id)
            assert org is not None
            org.status, org.archived_at = "archived", datetime.now(UTC)
    await db.commit()

    with pytest.raises(DispatchDeniedError) as denied:
        await _permit(lease)
    assert denied.value.code == code


@pytest.mark.asyncio
async def test_unprepared_descriptor_and_terminalized_lease_deny_without_sql(
    db: AsyncSession,
) -> None:
    graph = await seed_graph(db)
    registry, lease = await _active_lease(graph)

    with pytest.raises(DispatchDeniedError) as unknown:
        await _permit(lease, "tool:unknown")
    await registry.terminalize(lease.binding.run_id)
    with pytest.raises(DispatchDeniedError) as missing:
        await _permit(lease)

    assert unknown.value.code == "descriptor_not_prepared"
    assert missing.value.code == "prepared_run_lease_missing"


@pytest.mark.asyncio
async def test_archived_org_without_the_exact_lease_is_denied(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    _registry, lease = await _active_lease(graph)
    other_binding = PreparedRunBinding(
        graph.context.tenant_id,
        graph.context.org_id,
        graph.context.user_id,
        uuid.uuid4(),
        graph.conversation_ids[1],
        uuid.uuid4(),
        "thread",
    )
    organization = await db.get(Organization, graph.context.org_id)
    assert organization is not None
    organization.status, organization.archived_at = "archived", datetime.now(UTC)
    await db.commit()
    forged = kit.lease(other_binding, "forged-token")

    with pytest.raises(DispatchDeniedError) as denied:
        await _permit(forged)
    assert denied.value.code == "prepared_run_lease_missing"
    assert (await _permit(lease)).archived_bypass is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["provider", "summary", "tool", "backend"])
async def test_independent_job_rechecks_its_bound_run_before_each_leaf(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, kind: PermitKind
) -> None:
    graph = await seed_graph(db)
    _, lease = await _active_lease(graph, independent_job=True)
    check = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(
        "app.authz.independent_job_execution.authorize_independent_job_execution", check
    )

    sent: list[str] = []

    async def send_once() -> None:
        async with open_dispatch_permit(
            lease,
            kind=kind,
            descriptor_id="tool:search",
            allowed_descriptors=DESCRIPTORS,
            session_factory=TestSession,
        ):
            sent.append("effect")

    await send_once()
    with pytest.raises(DispatchDeniedError, match="independent_job_unavailable"):
        await send_once()
    assert sent == ["effect"]
    assert check.await_count == 2
    for call in check.await_args_list:
        assert call.kwargs == {
            "run_id": lease.binding.run_id,
            "conversation_id": lease.binding.conversation_id,
            "context": graph.context,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["child_launch", "child_control"])
async def test_independent_job_cannot_redelegate_or_drop_its_binding(
    db: AsyncSession, kind: PermitKind
) -> None:
    graph = await seed_graph(db)
    _, lease = await _active_lease(graph, independent_job=True)
    with pytest.raises(DispatchDeniedError, match="independent_job_depth_exceeded"):
        async with open_dispatch_permit(
            lease,
            kind=kind,
            descriptor_id="root/researcher",
            allowed_descriptors=frozenset({"root/researcher"}),
            session_factory=TestSession,
        ):
            pytest.fail("nested task started")

    forged = replace(lease, binding=replace(lease.binding, independent_job=False))
    with pytest.raises(DispatchDeniedError, match="prepared_run_lease_mismatch"):
        await _permit(forged)


@pytest.mark.asyncio
async def test_independent_binding_on_an_ordinary_conversation_never_reaches_leaf(
    db: AsyncSession,
) -> None:
    graph = await seed_graph(db)
    _, lease = await _active_lease(graph, independent_job=True)
    with pytest.raises(DispatchDeniedError, match="independent_job_unavailable"):
        await _permit(lease)


@pytest.mark.asyncio
async def test_independent_config_refuses_direct_graph_construction(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    cfg = AgentConfig(
        provider="openai",
        model_name="unused",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id=str(graph.conversation_ids[0]),
        org_context=graph.context,
        independent_job=True,
    )
    with pytest.raises(PermissionError, match="independent_job_requires_lease"):
        await _prepare_runtime_components(
            cfg, is_trigger_mode=False, include_ask_user=True, include_agent_memory_file=True
        )
