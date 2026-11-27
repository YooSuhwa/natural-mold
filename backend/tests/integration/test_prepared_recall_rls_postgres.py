"""Prepared recall keeps an accepted run and its receipts in the caller's exact scope."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.organization_execution_proof import live_proof
from app.config import settings
from app.database import ShieldedAsyncSession, tenant_session, tenant_sessions
from app.models.conversation_run import ConversationRun
from app.models.memory import MemoryRecall, MemoryRecallRun, MemoryRecord
from app.models.organization import Organization, OrganizationMember, TenantMember
from app.services import memory_embedder, memory_recall
from tests.authz.execution_gateway_testkit import GatewayGraph, seed_graph
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as value:
        yield value


def _run(
    graph: GatewayGraph, *, parent_id: UUID | None = None, conversation_id: UUID | None = None
) -> ConversationRun:
    ctx = graph.context
    return ConversationRun(
        id=uuid4(),
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        org_id=ctx.org_id,
        conversation_id=conversation_id or graph.conversation_ids[0],
        agent_id=graph.agent_id,
        source="resume" if parent_id else "chat",
        status="queued",
        input_preview="세션 만료 시간",
        parent_run_id=parent_id,
    )


def _fact(ctx: OrgContext, body: str) -> MemoryRecord:
    return MemoryRecord(
        id=uuid4(),
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        scope="user",
        content=body,
        store_path="/memories/user",
        origin="user_stated",
        approved_by=ctx.user_id,
        approval_channel="proposal",
        approved_at=datetime.now(UTC).replace(tzinfo=None),
    )


def _fail_if_ranked() -> NoReturn:
    pytest.fail("Scope rejection must precede detached recall reads")


async def test_prepared_recall_separates_owner_organization_and_company(
    lab: RlsLab,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = create_async_engine(async_url(lab.admin_url))
    app = create_async_engine(async_url(lab.app_url), pool_size=3)
    maker = async_sessionmaker(app, class_=ShieldedAsyncSession, expire_on_commit=False)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(memory_embedder, "async_session", maker)
    try:
        async with AsyncSession(admin, expire_on_commit=False) as seed:
            home = await seed_graph(seed, conversations=2)
            foreign = await seed_graph(seed, conversations=1)
            owner = home.context
            other_org = Organization(
                tenant_id=owner.tenant_id,
                name="Other organization",
                slug=uuid4().hex,
            )
            seed.add(other_org)
            await seed.flush()
            sibling = replace(owner, org_id=other_org.id)
            teammate = replace(owner, user_id=foreign.context.user_id)
            visitor = replace(foreign.context, user_id=owner.user_id)
            for member in (teammate, visitor):
                seed.add(TenantMember(tenant_id=member.tenant_id, user_id=member.user_id))
            await seed.flush()
            for member in (sibling, teammate, visitor):
                seed.add(
                    OrganizationMember(
                        org_id=member.org_id,
                        tenant_id=member.tenant_id,
                        user_id=member.user_id,
                    )
                )
            run, hidden = _run(home), _run(foreign)
            old_turn = _run(home, conversation_id=home.conversation_ids[1])
            seed.add_all([run, hidden, old_turn])
            await seed.flush()
            # Imported resume with an RLS-hidden parent or a visible parent from
            # an unrelated conversation. Neither is a new authorized recall root.
            invalid_resumes = [_run(home, parent_id=row.id) for row in (hidden, old_turn)]
            seed.add_all(invalid_resumes)
            found = _fact(owner, "세션 만료 시간은 30분이다")
            seed.add(found)
            for wrong in (sibling, teammate, visitor):
                seed.add(_fact(wrong, "세션 만료 시간은 5분이다"))
            await seed.commit()
        filters = memory_recall.Filters(scopes="user", agent_id=home.agent_id)

        async def assert_denied(other: OrgContext) -> None:
            async with tenant_session(other, maker=maker) as denied:
                proof = await acquire_execution_proof(denied, other)
                result = await memory_recall.prepared_recall_for_run(
                    denied,
                    _fail_if_ranked,
                    other,
                    run_id=str(run.id),
                    filters=filters,
                )
                assert (result.mode, result.records, result.block) == ("none", (), "")
                assert live_proof(denied) is proof
                await denied.commit()

        for other in (sibling, teammate, visitor):
            await assert_denied(other)

        target = RunConversationTarget(run.conversation_id, home.agent_id, owner)
        receipts = select(func.count()).select_from(MemoryRecallRun)
        details = select(MemoryRecall.record_id)
        for malformed in invalid_resumes:
            async with tenant_session(owner, maker=maker) as locked:
                assert await lock_executable_run(locked, RunExecutionTarget(malformed.id, target))
                proof = live_proof(locked)
                assert proof is not None
                result = await memory_recall.prepared_recall_for_run(
                    locked,
                    _fail_if_ranked,
                    owner,
                    run_id=str(malformed.id),
                    filters=filters,
                )
                assert (result.mode, result.records, result.block) == ("none", (), "")
                assert live_proof(locked) is proof
                await locked.commit()
        async with tenant_session(owner, maker=maker) as locked:
            role = (
                await locked.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, row_security_active('memory_recall_runs') "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
            ).one()
            assert tuple(role) == (False, False, True)
            assert await lock_executable_run(locked, RunExecutionTarget(run.id, target))
            proof = live_proof(locked)
            assert proof is not None
            assert await locked.scalar(receipts) == 0
            result = await memory_recall.prepared_recall_for_run(
                locked,
                tenant_sessions(owner, maker=maker),
                owner,
                run_id=str(run.id),
                filters=filters,
            )
            assert [record.id for record in result.records] == [found.id]
            assert result.mode == "fresh" and "30분" in result.block and "5분" not in result.block
            assert live_proof(locked) is proof
            async with tenant_session(owner, maker=maker) as observer:
                assert await observer.scalar(receipts) == 0
            await locked.commit()
        # An already recorded selection must not become reusable in another scope.
        for other in (sibling, teammate, visitor):
            await assert_denied(other)
        async with tenant_session(visitor, maker=maker) as other_company:
            assert await other_company.scalar(receipts) == 0
            assert list(await other_company.scalars(details)) == []
        async with AsyncSession(admin) as audit:
            assert list(await audit.scalars(select(MemoryRecallRun.run_id))) == [str(run.id)]
            assert list(await audit.scalars(details)) == [found.id]
            assert await audit.scalar(select(func.sum(MemoryRecord.recall_count))) == 1
    finally:
        await app.dispose()
        await admin.dispose()
