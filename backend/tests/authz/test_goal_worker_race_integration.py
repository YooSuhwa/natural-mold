"""Terminal reconciliation races on separate PostgreSQL sessions using ordinary chat admission."""

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.models import (
    Conversation,
    ConversationGoal,
    ConversationRun,
    ConversationRunInput,
    GoalAttempt,
    GoalTransitionIntent,
    OrganizationMember,
    TenantMember,
)
from app.schemas.conversation_goal import GoalWriteRequest
from app.services import conversation_goal_service as goals
from app.services import conversation_goal_worker as worker
from app.services.agent_execution_owner import OwnedExecutionRequest, accept_owned_execution
from app.services.artifacts.recorder import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.conversation_goal_evidence import inspect_goal_evidence
from app.services.conversation_run_queue_mutations import require_queue_executable
from app.services.conversation_run_service import transition_run
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.test_goal_rls_integration import _actor  # noqa: PLC2701

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as database:
        yield database


def _target(revision: int, path: str) -> GoalWriteRequest:
    return GoalWriteRequest.model_validate(
        {
            "expected_revision": revision,
            "title": f"Create {path}",
            "criteria": [{"kind": "nonempty_file", "logical_path": path}],
        }
    )


async def _no_steers(*_args: object) -> goals.GoalSteerState:
    # This integration composes i41 with the existing ordinary-chat worker.
    return goals.GoalSteerState(0, 0, False)


async def _terminal_run(
    session: Callable[[], AsyncSession],
    context: OrgContext,
    agent_id: UUID,
) -> tuple[UUID, UUID, UUID]:
    async with session() as db:
        conversation = Conversation(
            agent_id=agent_id,
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            agent_name_snapshot="Agent",
            last_activity_at=datetime.now(UTC),
            source="ui",
        )
        db.add(conversation)
        await db.flush()
        goal = await goals.save_goal(db, conversation, _target(0, "report.txt"))
        await db.commit()
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation.id,
                agent_id,
                context,
                "chat",
                "start",
            ),
        )
        run = await db.get(ConversationRun, accepted.run_id)
        assert run and (run.metadata_json or {}).get(worker.ATTEMPT_KEY)
        await transition_run(db, run, "running", worker_instance_id="first")
        await transition_run(db, run, "completed")
        await db.commit()
        return conversation.id, goal.id, run.id


async def _record_old_result(
    session: Callable[[], AsyncSession],
    context: OrgContext,
    agent_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    directory: Path,
) -> None:
    await asyncio.to_thread(directory.mkdir)
    recorder = ArtifactDeltaRecorder(
        session_factory=session,
        context=ArtifactRuntimeContext(conversation_id, context, agent_id, str(run_id), directory),
    )
    await recorder.prepare()
    await asyncio.to_thread(
        (directory / "report.txt").write_text, "Verified old result", encoding="utf-8"
    )
    assert await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="writer")
    async with session() as db:
        conversation = await db.get(Conversation, conversation_id)
        run = await db.get(ConversationRun, run_id)
        assert conversation and run
        evidence = await inspect_goal_evidence(
            db, conversation=conversation, run=run, criteria=_target(0, "report.txt").criteria
        )
        assert evidence.all_passed


@pytest.mark.parametrize("edit_first", [False, True], ids=["two-workers", "edit-before-worker"])
async def test_goal_settles_once_under_concurrent_transactions(
    lab: RlsLab,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    edit_first: bool,
) -> None:
    with lab.admin.begin() as setup:
        tenant, org, owner, agent = _actor(setup, lab.tenant_a)
        setup.execute(sa.insert(TenantMember).values(tenant_id=tenant, user_id=owner))
        setup.execute(
            sa.insert(OrganizationMember).values(org_id=org, user_id=owner, tenant_id=tenant)
        )
    scope = OrgContext(org, tenant, owner)
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({org}))
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr(goals, "_watermarks", _no_steers)
    engine = create_async_engine(async_url(lab.app_url))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    def session() -> AsyncSession:
        return tenant_session(scope, maker=maker)

    try:
        conversation_id, goal_id, run_id = await _terminal_run(session, scope, agent)
        if edit_first:
            await _record_old_result(
                session, scope, agent, conversation_id, run_id, tmp_path / "out"
            )

        async with session() as first, session() as second:
            holder = await first.scalar(sa.text("SELECT pg_backend_pid()"))
            waiter = await second.scalar(sa.text("SELECT pg_backend_pid()"))
            assert holder != waiter
            if edit_first:
                await require_queue_executable(first, conversation_id, scope)
                conversation = await first.get(Conversation, conversation_id)
                assert conversation
                await goals.save_goal(first, conversation, _target(1, "revised.txt"))
            else:
                await worker.reconcile_goal_run(first, run_id, conversation_id)

            async def settle_again() -> None:
                await worker.reconcile_goal_run(second, run_id, conversation_id)
                await second.commit()

            async with asyncio.timeout(15), asyncio.TaskGroup() as group:
                concurrent = group.create_task(settle_again())
                # Observe actual database contention before allowing the first owner to commit.
                while not await first.scalar(
                    sa.text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                    {"holder": holder, "waiter": waiter},
                ):
                    if concurrent.done():
                        await concurrent
                        pytest.fail(
                            "Second worker returned without waiting for the first transaction"
                        )
                    await asyncio.sleep(0.02)
                await first.commit()

        async with session() as db:
            goal = await db.get(ConversationGoal, goal_id)
            assert goal and goal.auto_followups == 1 and goal.status == "active"
            assert goal.version == (2 if edit_first else 1)
            receipts = (
                await db.scalars(
                    sa.select(GoalAttempt)
                    .where(GoalAttempt.goal_id == goal_id)
                    .order_by(GoalAttempt.goal_version)
                )
            ).all()
            assert [r.status for r in receipts] == (
                ["superseded", "followup_reserved"] if edit_first else ["followup_reserved"]
            )
            [queued] = (
                await db.scalars(
                    sa.select(ConversationRunInput).where(
                        ConversationRunInput.conversation_id == conversation_id
                    )
                )
            ).all()
            assert (
                receipts[-1].goal_version,
                receipts[-1].run_id,
                receipts[-1].followup_ordinal,
                receipts[-1].followup_input_id,
            ) == (goal.version, run_id, 1, queued.id)
            assert queued.status == "pending" and queued.source == worker.GOAL_INPUT_SOURCE
            assert not await db.scalar(
                sa.select(GoalTransitionIntent.id).where(GoalTransitionIntent.goal_id == goal_id)
            )
            if edit_first:
                assert receipts[-1].requires_fresh_run and goal.last_evidence is None
    finally:
        await engine.dispose()
