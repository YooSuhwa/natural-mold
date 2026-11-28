"""Chat resume and background approval recording serialize on the private conversation."""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import delete, insert, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.approvals import recording, service
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.models import (
    ApprovalRequest,
    Conversation,
    ConversationRun,
    Notification,
    OrganizationMember,
    TenantMember,
)
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.conversation_run_state import transition_run
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.test_goal_rls_integration import _actor  # noqa: PLC2701

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_INTERRUPT = "document-approval"


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as database:
        yield database


@pytest.fixture
async def engine(lab: RlsLab, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    value = create_async_engine(async_url(lab.app_url))
    try:
        yield value
    finally:
        await value.dispose()


@pytest.fixture
async def interrupted(lab: RlsLab, engine: AsyncEngine) -> tuple[OrgContext, UUID, UUID, UUID]:
    with lab.admin.begin() as setup:
        tenant, org, owner, agent = _actor(setup, lab.tenant_a)
        setup.execute(insert(TenantMember).values(tenant_id=tenant, user_id=owner))
        setup.execute(
            insert(OrganizationMember).values(org_id=org, user_id=owner, tenant_id=tenant)
        )
    ctx = OrgContext(org, tenant, owner)
    async with tenant_session(ctx, maker=async_sessionmaker(engine, expire_on_commit=False)) as db:
        room = Conversation(
            agent_id=agent,
            user_id=owner,
            org_id=org,
            tenant_id=tenant,
            last_activity_at=datetime.now(UTC),
            agent_name_snapshot="Agent",
        )
        db.add(room)
        await db.commit()
        accepted = await accept_owned_execution(
            db, OwnedExecutionRequest(room.id, agent, ctx, "chat", input_preview=None)
        )
        run = await db.get(ConversationRun, accepted.run_id)
        assert run is not None
        await transition_run(db, run, "running", worker_instance_id="approval-test")
        await transition_run(db, run, "interrupted", interrupt_id=_INTERRUPT)
        await db.commit()
        return ctx, agent, room.id, run.id


async def wait_for_pg_block(
    probe: AsyncConnection, waiter: int, blocker: int, second_finished: Callable[[], bool]
) -> None:
    async with asyncio.timeout(10):
        while not await probe.scalar(  # noqa: ASYNC110 - wait for a real PostgreSQL lock
            text("SELECT :blocker = ANY(pg_blocking_pids(:waiter))"),
            {"blocker": blocker, "waiter": waiter},
        ):
            if second_finished():
                pytest.fail("The second operation completed before the first committed")
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("record_first", [False, True], ids=["resume-first", "recorder-first"])
async def test_approval_recording_and_resume_cannot_leave_a_phantom_request(
    engine: AsyncEngine,
    interrupted: tuple[OrgContext, UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    record_first: bool,
) -> None:
    ctx, agent_id, room_id, parent_id = interrupted
    first_ready, release_first = asyncio.Event(), asyncio.Event()
    pending = service.PendingInterrupt(
        _INTERRUPT,
        {
            "action_requests": [{"name": "export", "args": {"format": "pptx"}}],
            "review_configs": [
                {"action_name": "export", "allowed_decisions": ["approve", "reject"]}
            ],
        },
    )

    async def checkpoint(thread_id: UUID) -> list[service.PendingInterrupt]:
        assert thread_id == room_id
        if record_first:
            first_ready.set()
            await release_first.wait()
        return [pending]

    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    async with (
        engine.connect() as recorder,
        engine.connect() as responder,
        engine.connect() as probe,
    ):
        recorder_pid = await recorder.scalar(text("SELECT pg_backend_pid()"))
        responder_pid = await responder.scalar(text("SELECT pg_backend_pid()"))
        assert recorder_pid != responder_pid
        await recorder.commit()
        await responder.commit()
        monkeypatch.setattr(
            recording, "async_session", async_sessionmaker(recorder, expire_on_commit=False)
        )

        async def resume() -> UUID:
            async with tenant_session(
                ctx, maker=async_sessionmaker(responder, expire_on_commit=False)
            ) as db:
                accepted = await accept_owned_execution(
                    db,
                    OwnedExecutionRequest(
                        room_id,
                        agent_id,
                        ctx,
                        "chat",
                        input_preview=None,
                        run_source="resume",
                        parent_run_id=parent_id,
                        interrupt_id=_INTERRUPT,
                    ),
                )
                await recording.claim_resume(
                    db,
                    parent_run_id=parent_id,
                    responses={_INTERRUPT: {"decisions": [{"type": "approve"}]}},
                    actor_id=ctx.user_id,
                    resumed_run_id=accepted.run_id,
                )
                if not record_first:
                    first_ready.set()
                    await release_first.wait()
                await db.commit()
                return accepted.run_id

        async with asyncio.TaskGroup() as group:
            if record_first:
                written = group.create_task(recording.record_interrupted_run(parent_id, ctx))
                await asyncio.wait_for(first_ready.wait(), timeout=10)
                resumed = group.create_task(resume())
                waiter, blocker = responder_pid, recorder_pid
            else:
                resumed = group.create_task(resume())
                await asyncio.wait_for(first_ready.wait(), timeout=10)
                written = group.create_task(recording.record_interrupted_run(parent_id, ctx))
                waiter, blocker = recorder_pid, responder_pid
            try:
                await wait_for_pg_block(
                    probe, waiter, blocker, (resumed if record_first else written).done
                )
            finally:
                release_first.set()

        assert await recording.record_interrupted_run(parent_id, ctx) == []
        async with tenant_session(
            ctx, maker=async_sessionmaker(responder, expire_on_commit=False)
        ) as db:
            rows = (
                await db.scalars(select(ApprovalRequest).where(ApprovalRequest.run_id == parent_id))
            ).all()
            successors = (
                await db.scalars(
                    select(ConversationRun.id).where(ConversationRun.parent_run_id == parent_id)
                )
            ).all()
            assert successors == [resumed.result()]
            if record_first:
                [row] = rows
                assert [item.id for item in written.result()] == [row.id]
                assert (row.status, row.channel, row.decisions, row.resumed_run_id) == (
                    "approved",
                    "chat",
                    ["approve"],
                    resumed.result(),
                )
            else:
                assert not rows and not written.result()


@pytest.mark.parametrize("already_committed", [True, False], ids=["committed", "in-flight"])
async def test_revoked_recipient_cannot_read_checkpoint_or_record_approval(
    lab: RlsLab,
    engine: AsyncEngine,
    interrupted: tuple[OrgContext, UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    already_committed: bool,
) -> None:
    ctx, _, room_id, run_id = interrupted
    visited: list[UUID] = []

    async def checkpoint(thread_id: UUID) -> list[service.PendingInterrupt]:
        visited.append(thread_id)
        return [service.PendingInterrupt(_INTERRUPT, {"action_requests": [{"name": "export"}]})]

    monkeypatch.setattr(recording, "checkpoint_interrupts", checkpoint)
    async with (
        engine.connect() as recorder,
        engine.connect() as revoker,
        engine.connect() as probe,
        tenant_session(
            ctx, maker=async_sessionmaker(revoker, expire_on_commit=False)
        ) as revocation,
    ):
        recorder_pid = await recorder.scalar(text("SELECT pg_backend_pid()"))
        revoker_pid = await revocation.scalar(text("SELECT pg_backend_pid()"))
        assert recorder_pid != revoker_pid
        await recorder.commit()
        monkeypatch.setattr(
            recording, "async_session", async_sessionmaker(recorder, expire_on_commit=False)
        )
        deleted = await revocation.scalar(
            delete(OrganizationMember)
            .where(
                OrganizationMember.tenant_id == ctx.tenant_id,
                OrganizationMember.org_id == ctx.org_id,
                OrganizationMember.user_id == ctx.user_id,
            )
            .returning(OrganizationMember.user_id)
        )
        assert deleted == ctx.user_id
        if already_committed:
            await revocation.commit()
            assert await recording.record_interrupted_run(run_id, ctx) == []
        else:
            async with asyncio.TaskGroup() as group:
                written = group.create_task(recording.record_interrupted_run(run_id, ctx))
                try:
                    await wait_for_pg_block(probe, recorder_pid, revoker_pid, written.done)
                    assert not visited
                finally:
                    await revocation.commit()
            assert written.result() == []

    assert not visited
    with lab.admin.connect() as audit:
        assert (
            audit.scalar(
                select(ConversationRun.conversation_id).where(ConversationRun.id == run_id)
            )
            == room_id
        )
        assert (
            audit.scalar(select(ApprovalRequest.id).where(ApprovalRequest.run_id == run_id)) is None
        )
        assert (
            audit.scalar(select(Notification.id).where(Notification.user_id == ctx.user_id)) is None
        )
