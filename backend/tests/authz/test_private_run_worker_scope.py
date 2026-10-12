"""Direct run workers reject stale authority before registration or execution."""

from collections.abc import AsyncGenerator
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.organization import OrganizationMember
from app.services import conversation_run_service as runs
from app.services import conversation_run_worker as worker
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


async def test_direct_worker_rejects_foreign_org_before_registering_task(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a queued run and a config captured in another organization.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    context = unit_context(TEST_USER_ID)
    run = await runs.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        context=context,
        source="chat",
        input_preview="Fixture",
    )
    await db.commit()
    cfg = AgentConfig(
        provider="openai",
        model_name="fixture",
        api_key=None,
        base_url=None,
        system_prompt="Fixture",
        tools_config=[],
        thread_id=str(conversation_id),
        agent_id=str(conversation.agent_id),
        user_id=str(TEST_USER_ID),
        org_context=replace(context, org_id=uuid4()),
    )
    registry = worker.RunTaskRegistry(worker_instance_id="scope-fixture")
    monkeypatch.setattr(worker, "async_session", TestSession)

    async def executor(_cfg: AgentConfig) -> AsyncGenerator[str, None]:
        raise AssertionError("Unauthorized executor must never run")
        yield ""  # pragma: no cover

    # When the direct worker entrypoint receives the mismatched context.
    try:
        with pytest.raises(AppError) as rejected:
            await worker.start_conversation_run(
                run_id=run.id,
                conversation_id=conversation_id,
                cfg=cfg,
                user=CurrentUser(id=TEST_USER_ID, email="fixture@test.dev", name="Fixture"),
                input_payload=[],
                moldy_source="chat",
                executor_fn=executor,
                registry=registry,
            )
        # Then no task is registered and the persisted run remains queued.
        assert rejected.value.status == 404 and registry.active_run_ids() == set()
        await db.refresh(run)
        assert run.status == "queued" and run.worker_instance_id is None
    finally:
        await registry.shutdown()


async def test_worker_rechecks_membership_at_running_transition(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.authz.conversation_run_scope import RunConversationTarget, RunExecutionTarget

    # Given a queued run and an execution target captured while membership was active.
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    context = unit_context(TEST_USER_ID)
    run = await runs.create_run(
        db,
        conversation_id=conversation_id,
        agent_id=conversation.agent_id,
        context=context,
        source="chat",
        input_preview="Revocation fixture",
    )
    target = RunExecutionTarget(
        run.id, RunConversationTarget(conversation_id, conversation.agent_id, context)
    )
    await db.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.org_id == context.org_id,
            OrganizationMember.user_id == context.user_id,
        )
        .values(status="suspended")
    )
    await db.commit()
    monkeypatch.setattr(worker, "async_session", TestSession)

    # When the detached worker tries to claim execution after revocation.
    loaded, started = await worker._transition_to_running(target, worker_instance_id="revoked")

    # Then it aborts before running while preserving durable cancellation maintenance.
    assert loaded is None and started is False
    await db.refresh(run)
    assert (run.status, run.is_active, run.worker_instance_id, run.cancel_reason) == (
        "canceling",
        True,
        None,
        "authority_revoked",
    )
    assert run.cancel_requested_at is not None
    assert run.cancellation_acknowledged_at is None
