"""Captured valid memory scope must not authorize durable writes after membership revocation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.agent import Agent
from app.models.memory import MemoryProposal, MemoryRecord
from app.models.organization import OrganizationMember
from app.schemas.memory import (
    AgentMemorySettingsUpdate,
    MemoryProposalCreate,
    MemoryRecordCreate,
    MemoryRecordUpdate,
)
from app.services import agent_memory_settings, memory_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_secret_collector import CollectorCase
pytest_plugins = ["tests.authz.test_private_secret_collector"]


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_memory_mutation_denies_revoked_captured_membership(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    operation: Literal["update", "delete"],
) -> None:
    own = private_scopes.first
    record = await memory_service.create_memory_record(
        db,
        context=own,
        payload=MemoryRecordCreate(scope="user", content="Owned fixture"),
    )
    assert record is not None
    control = await memory_service.update_memory_record(
        db,
        memory_id=record.id,
        context=own,
        payload=MemoryRecordUpdate(content="Owned control"),
    )
    assert control is not None and control.content == "Owned control"
    assert (
        await memory_service.delete_memory_record(
            db, memory_id=record.id, context=private_scopes.second
        )
        is False
    )
    await db.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == own.org_id,
            OrganizationMember.user_id == own.user_id,
        )
        .values(status="suspended")
    )
    await db.commit()
    denied = False
    try:
        match operation:
            case "update":
                await memory_service.update_memory_record(
                    db,
                    memory_id=record.id,
                    context=own,
                    payload=MemoryRecordUpdate(content="Revoked overwrite"),
                )
            case "delete":
                await memory_service.delete_memory_record(db, memory_id=record.id, context=own)
            case unreachable:
                assert_never(unreachable)
    except AppError:
        denied = True
    await db.commit()
    persisted = (
        await db.execute(
            select(MemoryRecord.content, MemoryRecord.status).where(MemoryRecord.id == record.id)
        )
    ).one()
    unchanged = persisted.content == "Owned control" and persisted.status == "active"
    assert denied and unchanged


async def test_reject_proposal_denies_revoked_membership(
    db: AsyncSession, private_scopes: PrivateScopes
) -> None:
    own = private_scopes.first
    proposal = await memory_service.create_memory_proposal(
        db, context=own, payload=MemoryProposalCreate(scope="user", content="Owned proposal")
    )
    assert proposal is not None
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()
    with pytest.raises(AppError):
        await memory_service.reject_memory_proposal(db, proposal_id=proposal.id, context=own)
    await db.commit()
    status = await db.scalar(select(MemoryProposal.status).where(MemoryProposal.id == proposal.id))
    assert status == "pending"


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_agent_settings_denies_revoked_membership(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    collector_case: CollectorCase,
    operation: Literal["create", "update"],
) -> None:
    own = private_scopes.first
    agent = await db.get(Agent, collector_case.conversation.agent_id)
    assert agent is not None
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()
    match operation:
        case "create":
            pending = agent_memory_settings.get_agent_settings(db, agent.id, own)
        case "update":
            pending = agent_memory_settings.update_agent_settings(
                db, agent.id, own, AgentMemorySettingsUpdate(memory_policy_override="off")
            )
        case unreachable:
            assert_never(unreachable)
    with pytest.raises(AppError):
        await pending
