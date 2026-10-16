"""Every leaf dispatch re-proves authority; only the exact lease bypasses archive."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError, open_dispatch_permit
from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.conftest import TestSession

DESCRIPTORS = frozenset({"tool:search", "provider:root"})


async def _active_lease(graph: GatewayGraph):
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
