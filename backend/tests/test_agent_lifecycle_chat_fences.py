from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.routers.conversation_agent_protocol_contracts import HistoryRequest
from app.routers.conversation_agent_protocol_state import load_thread_history_response
from app.services import (
    agent_execution_owner,
    conversation_run_queue_service,
    conversation_run_worker,
)
from app.services.agent_execution_owner import OwnedExecutionRequest
from app.services.chat.conversations import create_conversation
from tests.agent_lifecycle_chat_fences_support import (
    assert_owned_chat_acceptance_preserves_resume_contract_and_participants,
    assert_purged_run_input_mutation_routes_return_typed_lifecycle_denial,
    assert_workerless_recovery_finalizer_remains_available_after_archive,
)
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent
from tests.test_agent_lifecycle_run_ownership import _seed_execution_tree


async def _archive_agent(db: AsyncSession, conversation_id: UUID) -> Agent:
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    agent = await db.get(Agent, conversation.agent_id)
    assert agent is not None
    agent.archived_at = datetime.now(UTC).replace(tzinfo=None)
    agent.archived_by = TEST_USER_ID
    await db.commit()
    return agent


@pytest.mark.asyncio
async def test_archived_agent_denies_new_conversation_and_enqueue_before_writes(
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    agent = await _archive_agent(db, conversation_id)

    with pytest.raises(AppError) as create_error:
        await create_conversation(db, agent.id, context=unit_context(TEST_USER_ID))
    with pytest.raises(AppError) as enqueue_error:
        await conversation_run_queue_service.enqueue_input(
            db,
            conversation_id=conversation_id,
            context=unit_context(TEST_USER_ID),
            client_request_id="archived-enqueue",
            source="chat",
            input_payload={"messages": [{"role": "user", "content": "blocked"}]},
            attachment_ids=[],
            checkpoint_id=None,
            priority=0,
        )

    assert create_error.value.code == enqueue_error.value.code == "AGENT_ARCHIVED"
    assert (
        await db.scalar(
            select(func.count())
            .select_from(ConversationRunInput)
            .where(ConversationRunInput.client_request_id == "archived-enqueue")
        )
        == 0
    )


@pytest.mark.asyncio
async def test_archived_agent_denies_claim_without_mutating_exact_pending_revision(
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=unit_context(TEST_USER_ID),
        client_request_id="claim-after-archive",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "later"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=7,
    )
    await db.commit()
    original_revision = queued.revision
    await _archive_agent(db, conversation_id)

    with pytest.raises(AppError) as caught:
        await conversation_run_queue_service.claim_next_input(
            db,
            conversation_id=conversation_id,
            context=unit_context(TEST_USER_ID),
        )

    assert caught.value.code == "AGENT_ARCHIVED"
    await db.refresh(queued)
    assert (queued.status, queued.run_id, queued.revision) == (
        "pending",
        None,
        original_revision,
    )
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 0


@pytest.mark.asyncio
async def test_archived_agent_denies_worker_registration_before_executor(
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    run = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=conversation.agent_id,
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="accepted before archive",
        ),
    )
    await db.commit()
    cfg = await run_fixture_config(run.run_id, TestSession)
    await _archive_agent(db, conversation_id)
    executor_calls = 0

    async def executor(
        _cfg: AgentConfig, _payload: object, **_kwargs: object
    ) -> AsyncGenerator[str, None]:
        nonlocal executor_calls
        executor_calls += 1
        if False:
            yield ""

    with pytest.raises(AppError) as caught:
        await conversation_run_worker.start_conversation_run(
            run_id=run.run_id,
            conversation_id=conversation_id,
            cfg=cfg,
            user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
            input_payload={"messages": []},
            moldy_source="chat",
            executor_fn=executor,
        )

    assert caught.value.code == "AGENT_ARCHIVED"
    assert executor_calls == 0
    assert conversation_run_worker.get_run_task_registry().get(run.run_id) is None


@pytest.mark.asyncio
async def test_owned_resume_rejects_legacy_shape_unless_explicitly_allowed(
    db: AsyncSession,
) -> None:
    root, _child, conversation = await _seed_execution_tree(db)
    await db.commit()
    root_id, owner_id, conversation_id = root.id, root.user_id, conversation.id

    with pytest.raises(HTTPException) as rejected:
        await agent_execution_owner.accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=conversation_id,
                root_agent_id=root_id,
                context=unit_context(owner_id),
                source="chat",
                run_source="resume",
                input_preview=None,
                interrupt_id="legacy-interrupt",
            ),
        )
    assert rejected.value.status_code == 409
    await db.rollback()

    accepted = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation_id,
            root_agent_id=root_id,
            context=unit_context(owner_id),
            source="chat",
            run_source="resume",
            input_preview=None,
            interrupt_id="legacy-interrupt",
            allow_legacy_resume=True,
        ),
    )
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None and run.source == "resume" and run.parent_run_id is None


@pytest.mark.asyncio
async def test_owned_chat_acceptance_preserves_resume_contract_and_participants(
    db: AsyncSession,
) -> None:
    await assert_owned_chat_acceptance_preserves_resume_contract_and_participants(db)


@pytest.mark.asyncio
async def test_workerless_recovery_finalizer_remains_available_after_archive(
    db: AsyncSession,
) -> None:
    await assert_workerless_recovery_finalizer_remains_available_after_archive(db)


@pytest.mark.asyncio
async def test_purged_run_input_mutation_routes_return_typed_lifecycle_denial(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await assert_purged_run_input_mutation_routes_return_typed_lifecycle_denial(client, db)


@pytest.mark.asyncio
async def test_archived_history_and_queue_input_reads_remain_available(
    db: AsyncSession,
) -> None:
    conversation_id = await seed_conversation_with_agent()
    await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=unit_context(TEST_USER_ID),
        client_request_id="retained-history-input",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "retained"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    await _archive_agent(db, conversation_id)
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None

    inputs = await conversation_run_queue_service.list_inputs(
        db,
        conversation_id=conversation_id,
        context=unit_context(TEST_USER_ID),
    )
    history = await load_thread_history_response(
        conversation,
        HistoryRequest(limit=20),
        db=db,
        user=CurrentUser(id=TEST_USER_ID, email="owner@test", name="Owner"),
        context=unit_context(TEST_USER_ID),
    )

    assert [item.client_request_id for item in inputs] == ["retained-history-input"]
    assert history == []
