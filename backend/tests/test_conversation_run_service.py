from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_artifact import ConversationArtifact
from app.models.conversation_run import ConversationRun
from app.models.conversation_run import utc_now_naive as run_utc_now_naive
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.models.user import User
from app.services import conversation_run_service
from tests.conftest import TEST_USER_ID


async def _seed_agent_and_conversation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    conversation_title: str = "Run lifecycle",
) -> tuple[Agent, Conversation]:
    user = User(id=user_id, email=f"{user_id}@test.local", name="Run Owner")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Run Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title=conversation_title)
    db.add(conversation)
    await db.flush()
    return agent, conversation


@pytest.mark.asyncio
async def test_create_run_rejects_second_active_run(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    first = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="first",
    )

    with pytest.raises(HTTPException) as exc:
        await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=agent.user_id,
            source="chat",
            input_preview="second",
        )

    assert first.is_active is True
    assert first.status == "queued"
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_run_translates_active_unique_race_to_conflict(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    await db.commit()

    async def raise_active_unique_violation(*_args, **_kwargs) -> None:
        raise IntegrityError(
            "insert conversation_runs",
            {},
            Exception("UNIQUE constraint failed: conversation_runs.conversation_id"),
        )

    monkeypatch.setattr(db, "flush", raise_active_unique_violation)

    with pytest.raises(HTTPException) as exc:
        await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=agent.user_id,
            source="chat",
            input_preview="racing request",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Conversation already has an active run"


@pytest.mark.asyncio
async def test_transition_completed_clears_active(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="hello",
    )

    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker-a")
    await conversation_run_service.transition_run(db, run, "completed")

    assert run.status == "completed"
    assert run.is_active is False
    assert run.completed_at is not None
    assert run.worker_instance_id == "worker-a"


@pytest.mark.asyncio
async def test_terminal_run_cannot_transition_to_running(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview=None,
    )
    await conversation_run_service.transition_run(db, run, "failed", error_code="boom")

    with pytest.raises(ValueError, match="Invalid run status transition"):
        await conversation_run_service.transition_run(db, run, "running")


@pytest.mark.asyncio
async def test_canceling_can_transition_to_canceled(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="cancel me",
    )

    await conversation_run_service.transition_run(db, run, "running")
    await conversation_run_service.transition_run(db, run, "canceling")
    await conversation_run_service.transition_run(db, run, "canceled")

    assert run.status == "canceled"
    assert run.is_active is False
    assert run.cancel_requested_at is not None
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_interrupted_is_terminal_and_resume_creates_new_run(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    interrupted = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="needs approval",
    )
    await conversation_run_service.transition_run(
        db,
        interrupted,
        "running",
    )
    await conversation_run_service.transition_run(
        db,
        interrupted,
        "interrupted",
        interrupt_id="intr-1",
    )

    resumed = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="resume",
        input_preview="approve",
        parent_run_id=interrupted.id,
        interrupt_id="intr-1",
    )

    assert interrupted.is_active is False
    assert interrupted.status == "interrupted"
    assert resumed.parent_run_id == interrupted.id
    assert resumed.status == "queued"
    assert resumed.is_active is True


@pytest.mark.asyncio
async def test_resume_requires_latest_interrupted_parent_run(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    older = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="older",
    )
    await conversation_run_service.transition_run(db, older, "running")
    await conversation_run_service.transition_run(db, older, "interrupted", interrupt_id="old")
    newer = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="newer",
    )
    await conversation_run_service.transition_run(db, newer, "running")
    await conversation_run_service.transition_run(db, newer, "interrupted", interrupt_id="new")

    with pytest.raises(HTTPException) as exc:
        await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=agent.user_id,
            source="resume",
            input_preview="resume old",
            parent_run_id=older.id,
            interrupt_id="old",
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_resume_rejects_mismatched_interrupt_id(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    interrupted = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="needs approval",
    )
    await conversation_run_service.transition_run(db, interrupted, "running")
    await conversation_run_service.transition_run(
        db,
        interrupted,
        "interrupted",
        interrupt_id="expected",
    )

    with pytest.raises(HTTPException) as exc:
        await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=agent.user_id,
            source="resume",
            input_preview="approve",
            parent_run_id=interrupted.id,
            interrupt_id="wrong",
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_resume_rejects_interrupted_parent_that_already_has_resume_child(
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    interrupted = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="needs approval",
    )
    await conversation_run_service.transition_run(db, interrupted, "running")
    await conversation_run_service.transition_run(
        db,
        interrupted,
        "interrupted",
        interrupt_id="approval-1",
    )
    first_resume = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="resume",
        input_preview=None,
        parent_run_id=interrupted.id,
        interrupt_id="approval-1",
    )
    await conversation_run_service.transition_run(db, first_resume, "running")
    await conversation_run_service.transition_run(db, first_resume, "completed")

    with pytest.raises(HTTPException) as exc:
        await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=agent.id,
            user_id=agent.user_id,
            source="resume",
            input_preview=None,
            parent_run_id=interrupted.id,
            interrupt_id="approval-1",
        )

    assert exc.value.status_code == 409
    assert (
        await conversation_run_service.current_run_for_conversation(
            db,
            conversation_id=conversation.id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_get_active_run_is_ownership_scoped(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="visible",
    )

    assert (
        await conversation_run_service.get_active_run(
            db,
            conversation_id=conversation.id,
            user_id=agent.user_id,
        )
    ) == run
    assert (
        await conversation_run_service.get_active_run(
            db,
            conversation_id=conversation.id,
            user_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        )
        is None
    )


@pytest.mark.asyncio
async def test_db_rejects_two_active_runs_for_same_conversation(db: AsyncSession) -> None:
    agent, conversation = await _seed_agent_and_conversation(db)
    db.add_all(
        [
            ConversationRun(
                conversation_id=conversation.id,
                agent_id=agent.id,
                user_id=agent.user_id,
                source="chat",
                status="running",
                is_active=True,
            ),
            ConversationRun(
                conversation_id=conversation.id,
                agent_id=agent.id,
                user_id=agent.user_id,
                source="chat",
                status="queued",
                is_active=True,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.asyncio
async def test_active_runs_for_conversations_batches_by_ids(db: AsyncSession) -> None:
    agent, first = await _seed_agent_and_conversation(db, conversation_title="first")
    second = Conversation(agent_id=agent.id, title="second")
    db.add(second)
    await db.flush()
    first_run = await conversation_run_service.create_run(
        db,
        conversation_id=first.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="first",
    )
    second_run = await conversation_run_service.create_run(
        db,
        conversation_id=second.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="second",
    )

    active = await conversation_run_service.active_runs_for_conversations(
        db,
        [first.id, second.id, uuid.uuid4()],
    )

    assert active == {first.id: first_run, second.id: second_run}
    assert set((await db.execute(select(ConversationRun))).scalars().all()) == {
        first_run,
        second_run,
    }


@pytest.mark.asyncio
async def test_mark_stale_active_runs_only_marks_old_active_runs(db: AsyncSession) -> None:
    agent, stale_conversation = await _seed_agent_and_conversation(
        db,
        conversation_title="stale run",
    )
    stale = await conversation_run_service.create_run(
        db,
        conversation_id=stale_conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="old",
    )
    await conversation_run_service.transition_run(
        db,
        stale,
        "running",
        worker_instance_id="worker-a",
    )
    stale.heartbeat_at = run_utc_now_naive() - timedelta(minutes=20)

    fresh_conversation = Conversation(agent_id=agent.id, title="fresh run")
    db.add(fresh_conversation)
    await db.flush()
    fresh = await conversation_run_service.create_run(
        db,
        conversation_id=fresh_conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="fresh",
    )
    await conversation_run_service.transition_run(
        db,
        fresh,
        "running",
        worker_instance_id="worker-a",
    )
    fresh.heartbeat_at = run_utc_now_naive()

    marked = await conversation_run_service.mark_stale_active_runs(
        db,
        stale_before=run_utc_now_naive() - timedelta(minutes=5),
        worker_instance_id="worker-a",
        include_workerless=True,
    )

    assert marked == 1
    assert stale.status == "stale"
    assert stale.is_active is False
    assert stale.error_code == "stale_heartbeat"
    assert fresh.status == "running"
    assert fresh.is_active is True


@pytest.mark.asyncio
async def test_mark_stale_active_runs_scopes_to_conversation(db: AsyncSession) -> None:
    agent, target_conversation = await _seed_agent_and_conversation(
        db,
        conversation_title="target sweep",
    )
    target = await conversation_run_service.create_run(
        db,
        conversation_id=target_conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="target",
    )
    await conversation_run_service.transition_run(
        db,
        target,
        "running",
        worker_instance_id="worker-a",
    )
    target.heartbeat_at = run_utc_now_naive() - timedelta(minutes=20)

    other_conversation = Conversation(agent_id=agent.id, title="other sweep")
    db.add(other_conversation)
    await db.flush()
    other = await conversation_run_service.create_run(
        db,
        conversation_id=other_conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="other",
    )
    await conversation_run_service.transition_run(
        db,
        other,
        "running",
        worker_instance_id="worker-a",
    )
    # 같은 worker 의 동일하게 오래된 run 이라도 conversation 스코프 밖이면 건드리지 않는다.
    other.heartbeat_at = run_utc_now_naive() - timedelta(minutes=20)

    marked = await conversation_run_service.mark_stale_active_runs(
        db,
        stale_before=run_utc_now_naive() - timedelta(minutes=5),
        worker_instance_id="worker-a",
        include_workerless=True,
        conversation_id=target_conversation.id,
    )

    assert marked == 1
    assert target.status == "stale"
    assert other.status == "running"
    assert other.is_active is True


@pytest.mark.asyncio
async def test_mark_stale_active_runs_finalizes_trace_and_artifacts(
    db: AsyncSession,
) -> None:
    agent, conversation = await _seed_agent_and_conversation(
        db,
        conversation_title="stale artifacts",
    )
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=agent.id,
        user_id=agent.user_id,
        source="chat",
        input_preview="artifact run",
    )
    await conversation_run_service.transition_run(
        db,
        run,
        "running",
        worker_instance_id="dead-worker",
    )
    run.heartbeat_at = run_utc_now_naive() - timedelta(minutes=20)
    record = MessageEvent(
        conversation_id=conversation.id,
        assistant_msg_id=str(run.id),
        events=[],
        last_event_id=None,
        status="streaming",
    )
    artifact = ConversationArtifact(
        user_id=agent.user_id,
        agent_id=agent.id,
        conversation_id=conversation.id,
        assistant_msg_id=str(run.id),
        logical_path="reports/demo.docx",
        display_name="demo.docx",
        extension="docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        artifact_kind="document",
        size_bytes=12,
        sha256="0" * 64,
        status="ready",
    )
    db.add_all([record, artifact])
    await db.flush()

    marked = await conversation_run_service.mark_stale_active_runs(
        db,
        stale_before=run_utc_now_naive() - timedelta(minutes=5),
        worker_instance_id=None,
        include_workerless=True,
    )

    assert marked == 1
    assert run.status == "stale"
    assert record.status == "failed"
    assert record.completed_at is not None
    assert artifact.status == "failed"
