"""Persisted memory parents gate sibling reads and mutations, including NULL history."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.memory import MemoryProposal, MemoryRecord
from app.models.organization import OrganizationMember
from app.schemas.memory import MemoryProposalCreate, MemoryRecordCreate
from app.services import memory_service
from app.services.chat.conversations import create_conversation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase

pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
ParentState = Literal[
    "null", "owned", "foreign_agent", "foreign_conversation", "foreign_conversation_agent"
]
RecordAction = Literal["get", "list", "runtime", "delete"]
ProposalAction = Literal["get", "reject", "approve"]


@pytest.mark.parametrize(
    "state",
    ["null", "owned", "foreign_agent", "foreign_conversation", "foreign_conversation_agent"],
)
@pytest.mark.parametrize("action", ["get", "list", "runtime", "delete"])
async def test_record_parent_boundary_for_sibling_consumers(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    state: ParentState,
    action: RecordAction,
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    other = await create_conversation(db, api_case.second.agent_id, context=private_scopes.second)
    record = await memory_service.create_memory_record(
        db,
        context=own,
        payload=MemoryRecordCreate(
            scope="user",
            content="Memory sibling fixture",
            source_conversation_id=conversation.id,
        ),
    )
    assert record is not None
    match state:
        case "null":
            changed = update(MemoryRecord).values(agent_id=None, source_conversation_id=None)
        case "foreign_conversation_agent":
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(agent_id=api_case.second.agent_id)
                .execution_options(synchronize_session=False)
            )
            changed = update(MemoryRecord).values(agent_id=api_case.first.agent_id)
        case "owned":
            changed = update(MemoryRecord).values(agent_id=api_case.first.agent_id)
        case "foreign_agent":
            changed = update(MemoryRecord).values(agent_id=api_case.second.agent_id)
        case "foreign_conversation":
            changed = update(MemoryRecord).values(source_conversation_id=other.id)
        case unreachable:
            assert_never(unreachable)
    await db.execute(
        changed.where(MemoryRecord.id == record.id).execution_options(synchronize_session=False)
    )
    await db.commit()
    allowed = state in {"null", "owned"}
    match action:
        case "get":
            result = await memory_service.get_memory_record(db, memory_id=record.id, context=own)
            assert (result is not None) == allowed
        case "list":
            rows = await memory_service.list_memory_records(db, context=own)
            assert any(row.id == record.id for row in rows) == allowed
        case "runtime":
            rows = await memory_service.list_runtime_memory_records(
                db, context=own, agent_id=api_case.first.agent_id, allowed_scopes="both"
            )
            assert any(row.id == record.id for row in rows) == allowed
        case "delete":
            deleted = await memory_service.delete_memory_record(
                db, memory_id=record.id, context=own
            )
            assert deleted == allowed
        case unreachable:
            assert_never(unreachable)
    status = await db.scalar(select(MemoryRecord.status).where(MemoryRecord.id == record.id))
    assert status == ("deleted" if action == "delete" and allowed else "active")


@pytest.mark.parametrize(
    "state",
    ["null", "owned", "foreign_agent", "foreign_conversation", "foreign_conversation_agent"],
)
@pytest.mark.parametrize("action", ["get", "reject", "approve"])
async def test_proposal_parent_boundary_for_sibling_consumers(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    api_case: ApiCase,
    state: ParentState,
    action: ProposalAction,
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    other = await create_conversation(db, api_case.second.agent_id, context=private_scopes.second)
    proposal = await memory_service.create_memory_proposal(
        db,
        context=own,
        payload=MemoryProposalCreate(
            scope="user",
            content="Proposal sibling fixture",
            agent_id=api_case.first.agent_id,
            conversation_id=conversation.id,
        ),
    )
    assert proposal is not None
    match state:
        case "null":
            changed = update(MemoryProposal).values(agent_id=None, conversation_id=None)
        case "foreign_conversation_agent":
            await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation.id)
                .values(agent_id=api_case.second.agent_id)
                .execution_options(synchronize_session=False)
            )
            changed = update(MemoryProposal).values(agent_id=api_case.first.agent_id)
        case "owned":
            changed = update(MemoryProposal).values(agent_id=api_case.first.agent_id)
        case "foreign_agent":
            changed = update(MemoryProposal).values(agent_id=api_case.second.agent_id)
        case "foreign_conversation":
            changed = update(MemoryProposal).values(conversation_id=other.id)
        case unreachable:
            assert_never(unreachable)
    await db.execute(
        changed.where(MemoryProposal.id == proposal.id).execution_options(synchronize_session=False)
    )
    await db.commit()
    allowed = state in {"null", "owned"}
    match action:
        case "get":
            loaded = await memory_service.get_memory_proposal(
                db, proposal_id=proposal.id, context=own
            )
            assert (loaded is not None) == allowed
            expected_status = "pending"
        case "reject":
            rejected = await memory_service.reject_memory_proposal(
                db, proposal_id=proposal.id, context=own
            )
            assert (rejected is not None) == allowed
            expected_status = "rejected" if allowed else "pending"
        case "approve":
            approved = await memory_service.approve_memory_proposal(
                db, proposal_id=proposal.id, context=own
            )
            assert (approved is not None) == allowed
            expected_status = "approved" if allowed else "pending"
        case unreachable:
            assert_never(unreachable)
    status = await db.scalar(select(MemoryProposal.status).where(MemoryProposal.id == proposal.id))
    assert status == expected_status
    records = list((await db.scalars(select(MemoryRecord))).all())
    assert len(records) == (1 if action == "approve" and allowed else 0)


@pytest.mark.parametrize("runtime", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
async def test_memory_lists_refresh_actor_before_hydration(
    db: AsyncSession, private_scopes: PrivateScopes, runtime: bool, revoked: bool
) -> None:
    own = private_scopes.first
    record = await memory_service.create_memory_record(
        db, context=own, payload=MemoryRecordCreate(scope="user", content="Current actor memory")
    )
    assert record is not None
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id,
                OrganizationMember.org_id == own.org_id,
            )
            .values(status="suspended")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    rows: list[MemoryRecord] = []
    denied = False
    try:
        if runtime:
            rows = await memory_service.list_runtime_memory_records(
                db, context=own, agent_id=None, allowed_scopes="both"
            )
        else:
            rows = await memory_service.list_memory_records(db, context=own)
    except AppError as error:
        denied = error.status == 403
    assert denied == revoked
    assert len(rows) == (0 if revoked else 1)


@pytest.mark.parametrize("proposal", [False, True])
async def test_memory_creators_reject_foreign_agent_of_owned_source_conversation(
    db: AsyncSession, private_scopes: PrivateScopes, api_case: ApiCase, proposal: bool
) -> None:
    own = private_scopes.first
    conversation = await create_conversation(db, api_case.first.agent_id, context=own)
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(agent_id=api_case.second.agent_id)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if proposal:
        with pytest.raises(AppError) as denied:
            await memory_service.create_memory_proposal(
                db,
                context=own,
                payload=MemoryProposalCreate(
                    scope="user", content="Blocked proposal", conversation_id=conversation.id
                ),
            )
    else:
        with pytest.raises(AppError) as denied:
            await memory_service.create_memory_record(
                db,
                context=own,
                payload=MemoryRecordCreate(
                    scope="user", content="Blocked record", source_conversation_id=conversation.id
                ),
            )
    assert denied.value.status == 404
    assert list((await db.scalars(select(MemoryRecord))).all()) == []
    assert list((await db.scalars(select(MemoryProposal))).all()) == []
