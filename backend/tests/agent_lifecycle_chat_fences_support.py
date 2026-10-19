from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import (
    agent_execution_owner,
    conversation_run_queue_service,
    conversation_run_worker,
)
from app.services.agent_execution_owner import OwnedExecutionRequest
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree


@pytest.mark.asyncio
async def assert_owned_chat_acceptance_preserves_resume_contract_and_participants(
    db: AsyncSession,
) -> None:
    root, child, conversation = await _seed_execution_tree(db)
    parent = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=root.id,
            context=unit_context(root.user_id),
            source="chat",
            input_preview="parent",
        ),
    )
    parent_run = await db.get(ConversationRun, parent.run_id)
    assert parent_run is not None
    parent_run.status = "interrupted"
    parent_run.is_active = False
    parent_run.interrupt_id = "interrupt-1"
    await db.flush()

    accepted = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=root.id,
            context=unit_context(root.user_id),
            source="chat",
            input_preview=None,
            run_source="resume",
            parent_run_id=parent.run_id,
            interrupt_id="interrupt-1",
            run_metadata={"protocol": "langgraph_v3"},
        ),
    )
    await db.commit()

    run = await db.get(ConversationRun, accepted.run_id)
    participants = tuple(
        await db.scalars(
            select(ConversationRunAgent).where(ConversationRunAgent.run_id == accepted.run_id)
        )
    )
    assert run is not None
    assert (run.source, run.parent_run_id, run.interrupt_id, run.metadata_json) == (
        "resume",
        parent.run_id,
        "interrupt-1",
        {"protocol": "langgraph_v3"},
    )
    assert {item.agent_id_snapshot for item in participants} == {root.id, child.id}


@pytest.mark.asyncio
async def assert_workerless_recovery_finalizer_remains_available_after_archive(
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    accepted = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation_id,
            root_agent_id=conversation.agent_id,
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="workerless",
        ),
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    await conversation_run_queue_service.persist_direct_input(
        db,
        run=run,
        context=unit_context(TEST_USER_ID),
        client_request_id="workerless-archive",
        input_payload={"messages": [{"role": "user", "content": "workerless"}]},
        attachment_ids=[],
    )
    await db.commit()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    agent = await db.get(Agent, conversation.agent_id)
    assert agent is not None
    agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
    agent.archived_by = TEST_USER_ID
    await db.commit()
    run = await db.get(ConversationRun, accepted.run_id, with_for_update=True)
    assert run is not None
    await conversation_run_worker.conversation_run_service.request_cancel_run(
        db, run, reason="agent_archived"
    )
    await db.commit()

    finalized = await finalize_workerless_cancel_before_start(
        session_factory=TestSession,
        protected_run_ids=(),
    )

    terminal = await db.get(ConversationRun, accepted.run_id, populate_existing=True)
    queued = await db.scalar(
        select(ConversationRunInput).where(ConversationRunInput.run_id.is_(None))
    )
    assert finalized == [conversation_id]
    assert terminal is not None and terminal.status == "canceled"
    assert terminal.cancellation_acknowledged_at is not None
    assert queued is not None and queued.status == "canceled" and queued.revision == 2


@pytest.mark.asyncio
async def assert_purged_run_input_mutation_routes_return_typed_lifecycle_denial(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=unit_context(TEST_USER_ID),
        client_request_id="purged-route-mutations",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "retained"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.agent_id = None
    conversation.agent_lifecycle_state = "purged"
    conversation.agent_purged_at = datetime.now(UTC).replace(tzinfo=None)
    conversation.queue_paused = True
    queued.agent_id = None
    await db.commit()

    base = f"/api/conversations/{conversation_id}/run-inputs"
    responses = [
        await client.patch(
            f"{base}/{queued.id}",
            json={"expected_revision": 1, "input": {"messages": []}},
        ),
        await client.delete(f"{base}/{queued.id}?expected_revision=1"),
        await client.post(f"{base}/{queued.id}/promote", json={"expected_revision": 1}),
        await client.post(
            f"{base}/reorder",
            json={
                "ordered_input_ids": [str(queued.id)],
                "expected_revisions": {str(queued.id): 1},
            },
        ),
        await client.post(f"{base}/resume"),
    ]

    assert [(response.status_code, response.json()["error"]["code"]) for response in responses] == [
        (409, "AGENT_ARCHIVED"),
    ] * 5
    await db.refresh(queued)
    assert (queued.status, queued.revision, queued.run_id) == ("pending", 1, None)
