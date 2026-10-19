"""Canonical gateway order, fresh authority and archive/lease admission."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    PreparedRunRegistry,
    reset_prepared_run_registry_for_tests,
)
from app.agent_runtime.runtime_config import PreparedRunBinding
from app.authz.execution_lock_cursor import ExecutionProofError, LockClass
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.exceptions import AppError
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from tests.authz import prepared_run_testkit as kit
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph


def _order(proof) -> list[LockClass]:
    classes: list[LockClass] = []
    for lock_class, _resource in proof.cursor._held:
        if not classes or classes[-1] != lock_class:
            classes.append(lock_class)
    return classes


@pytest.mark.asyncio
async def test_prefix_is_canonical_and_exact_reuse_is_sql_free(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    proof = await acquire_execution_proof(
        db, graph.context, root_agent_id=graph.agent_id, conversation_ids=graph.conversation_ids
    )
    statements: list[str] = []
    from sqlalchemy import event

    engine = db.bind.sync_engine if hasattr(db.bind, "sync_engine") else db.bind
    listener = lambda *args: statements.append(args[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        again = await acquire_execution_proof(
            db, graph.context, root_agent_id=graph.agent_id, conversation_ids=graph.conversation_ids
        )
    finally:
        event.remove(engine, "before_cursor_execute", listener)

    assert again is proof
    assert statements == []
    assert _order(proof) == [
        LockClass.TENANT,
        LockClass.ORGANIZATION,
        LockClass.TENANT_MEMBER,
        LockClass.ORGANIZATION_MEMBER,
        LockClass.USER,
        LockClass.AGENT_LIFECYCLE,
        LockClass.CONVERSATION,
    ]
    conversations = [r for c, r in proof.cursor._held if c is LockClass.CONVERSATION]
    assert conversations == sorted(conversations, key=lambda value: value.bytes)


@pytest.mark.asyncio
async def test_expansion_after_proof_is_denied(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    low, high = graph.conversation_ids
    await acquire_execution_proof(db, graph.context, conversation_ids=(high,))

    with pytest.raises(ExecutionProofError) as expanded:
        await acquire_execution_proof(db, graph.context, conversation_ids=(low,))
    assert expanded.value.code == "execution_scope_expansion_denied"


async def _mutate(db: AsyncSession, graph: GatewayGraph, case: str) -> None:
    ctx = graph.context
    if case == "tenant_suspended":
        tenant = await db.get(Tenant, ctx.tenant_id)
        assert tenant is not None
        tenant.status = "suspended"
    elif case == "company_member_removed":
        member = await db.get(TenantMember, (ctx.tenant_id, ctx.user_id))
        assert member is not None
        member.status = "removed"
    elif case == "org_member_suspended":
        member = await db.get(OrganizationMember, (ctx.org_id, ctx.user_id))
        assert member is not None
        member.status = "suspended"
    else:
        org = await db.get(Organization, ctx.org_id)
        assert org is not None
        org.status, org.archived_at = "archived", datetime.now(UTC)
    await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("tenant_suspended", "tenant_suspended"),
        ("company_member_removed", "forbidden"),
        ("org_member_suspended", "forbidden"),
        ("org_archived", "organization_unavailable"),
    ],
)
async def test_fresh_authority_denies_before_lower_locks(
    db: AsyncSession, case: str, code: str
) -> None:
    graph = await seed_graph(db)
    await _mutate(db, graph, case)

    with pytest.raises(AppError) as denied:
        await acquire_execution_proof(db, graph.context, root_agent_id=graph.agent_id)
    assert denied.value.code == code


@pytest.mark.asyncio
async def test_archive_requires_exact_active_lease(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    await _mutate(db, graph, "org_archived")
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
    try:
        with pytest.raises(AppError):
            await acquire_execution_proof(db, ctx, lease=lease)
        await db.rollback()
        registry.activate(binding.run_id, token=token, digest=lease.digest)
        proof = await acquire_execution_proof(db, ctx, lease=lease)
        assert proof.archived_bypass is True
    finally:
        reset_prepared_run_registry_for_tests()


@pytest.mark.asyncio
async def test_removal_denies_even_with_lease(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    await _mutate(db, graph, "org_archived")
    await _mutate(db, graph, "org_member_suspended")

    with pytest.raises(AppError) as denied:
        await acquire_execution_proof(db, graph.context)
    assert denied.value.code == "forbidden"


@pytest.mark.asyncio
async def test_archived_bypass_is_bound_to_the_leases_exact_run(db: AsyncSession) -> None:
    graph = await seed_graph(db)
    await _mutate(db, graph, "org_archived")
    registry = PreparedRunRegistry()
    reset_prepared_run_registry_for_tests(registry)
    ctx = graph.context
    own, other = graph.conversation_ids
    binding = PreparedRunBinding(
        ctx.tenant_id, ctx.org_id, ctx.user_id, graph.agent_id, own, uuid.uuid4(), "thread"
    )
    token = registry.reserve(binding)
    lease = kit.lease(binding, token)
    await registry.stage(lease)
    registry.activate(binding.run_id, token=token, digest=lease.digest)
    try:
        with pytest.raises(AppError):
            await acquire_execution_proof(db, ctx, conversation_ids=(other,), lease=lease)
        await db.rollback()
        with pytest.raises(AppError):
            await acquire_execution_proof(db, ctx, root_agent_id=uuid.uuid4(), lease=lease)
        await db.rollback()
        proof = await acquire_execution_proof(
            db, ctx, root_agent_id=graph.agent_id, conversation_ids=(own,), lease=lease
        )
        assert proof.archived_bypass is True
        with pytest.raises(ExecutionProofError):
            await acquire_execution_proof(db, ctx, conversation_ids=(other,))
    finally:
        reset_prepared_run_registry_for_tests()
