"""Memory records, proposal actions and source references use one explicit org scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.memory import MemoryRecord
from app.models.organization import OrganizationMember, Tenant
from app.models.user import User
from app.schemas.memory import MemoryProposalCreate, MemoryRecordCreate, MemoryRecordUpdate
from app.services import memory_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase

pytest_plugins = ["tests.authz.test_private_secret_collector"]


async def test_memory_records_are_private_to_the_exact_company(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    record = await memory_service.create_memory_record(
        db,
        context=private_scopes.first,
        payload=MemoryRecordCreate(scope="user", content="First company memory"),
    )
    assert record is not None
    assert (record.user_id, record.org_id, record.tenant_id) == (
        private_scopes.first.user_id,
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
    )
    assert await memory_service.list_memory_records(db, context=private_scopes.second) == []
    assert (
        await memory_service.update_memory_record(
            db,
            context=private_scopes.second,
            memory_id=record.id,
            payload=MemoryRecordUpdate(content="Foreign overwrite"),
        )
        is None
    )
    assert (
        await memory_service.delete_memory_record(
            db, context=private_scopes.second, memory_id=record.id
        )
        is False
    )
    await db.refresh(record)
    assert record.content == "First company memory" and record.status == "active"


async def test_proposal_approval_copies_the_same_private_scope(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    proposal = await memory_service.create_memory_proposal(
        db,
        context=private_scopes.first,
        payload=MemoryProposalCreate(scope="user", content="Proposed memory"),
    )
    assert proposal is not None
    assert (
        await memory_service.approve_memory_proposal(
            db, context=private_scopes.second, proposal_id=proposal.id
        )
        is None
    )
    assert (
        await memory_service.reject_memory_proposal(
            db, context=private_scopes.second, proposal_id=proposal.id
        )
        is None
    )
    approved = await memory_service.approve_memory_proposal(
        db, context=private_scopes.first, proposal_id=proposal.id
    )
    assert approved is not None
    saved_proposal, record = approved
    assert saved_proposal.status == "approved"
    assert (record.user_id, record.org_id, record.tenant_id) == (
        proposal.user_id,
        proposal.org_id,
        proposal.tenant_id,
    )


async def test_memory_rejects_foreign_source_conversation(
    db: AsyncSession, collector_case: CollectorCase, private_scopes: PrivateScopes
) -> None:
    with pytest.raises(AppError) as captured:
        await memory_service.create_memory_record(
            db,
            context=private_scopes.second,
            payload=MemoryRecordCreate(
                scope="user",
                content="Foreign source",
                source_conversation_id=collector_case.conversation.id,
            ),
        )
    assert captured.value.status == 404


async def test_runtime_memory_query_cannot_read_the_other_company(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    await memory_service.create_memory_record(
        db,
        context=private_scopes.first,
        payload=MemoryRecordCreate(scope="user", content="First runtime memory"),
    )
    assert (
        await memory_service.list_runtime_memory_records(
            db, context=private_scopes.second, agent_id=None, allowed_scopes="both"
        )
        == []
    )


@pytest.mark.parametrize("revocation", ["user", "membership", "company"])
async def test_memory_writer_rechecks_persisted_actor_before_write(
    db: AsyncSession, private_scopes: PrivateScopes, revocation: str
) -> None:
    context = private_scopes.first
    mutations = {
        "user": update(User).where(User.id == context.user_id).values(is_active=False),
        "membership": update(OrganizationMember)
        .where(
            OrganizationMember.user_id == context.user_id,
            OrganizationMember.org_id == context.org_id,
        )
        .values(status="suspended"),
        "company": update(Tenant).where(Tenant.id == context.tenant_id).values(status="suspended"),
    }
    await db.execute(mutations[revocation])
    await db.commit()
    with pytest.raises(AppError):
        await memory_service.create_memory_record(
            db,
            context=context,
            payload=MemoryRecordCreate(scope="user", content="Stale actor memory"),
        )
    assert list((await db.scalars(select(MemoryRecord))).all()) == []
