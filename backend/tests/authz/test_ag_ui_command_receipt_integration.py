"""AG-UI command admission under PostgreSQL row locks and a non-owner RLS role."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.authz.org_context import OrgContext
from app.config import settings
from app.database import platform_sessions, tenant_session
from app.exceptions import AppError, ConflictError
from app.models import (
    Conversation,
    ConversationRun,
    ConversationRunInput,
    OrganizationMember,
    TenantMember,
)
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.services import ag_ui_commands as commands
from app.services.conversation_run_cancellation_recovery import (
    finalize_workerless_cancel_before_start,
)
from app.services.conversation_run_queue_claim import claim_next_input
from app.services.conversation_run_queue_persistence import enqueue_input
from app.services.conversation_run_state import request_cancel_run
from tests.authz.i06_rls_lab import RlsLab, async_url, rls_lab
from tests.authz.test_goal_rls_integration import _actor  # noqa: PLC2701

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_PUBLIC = "test-ui:run-01"
_DIGEST = "1" * 64


@pytest.fixture(scope="module")
def lab() -> Iterator[RlsLab]:
    with rls_lab() as database:
        yield database


@pytest.fixture
async def sessions(
    lab: RlsLab, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    engine = create_async_engine(async_url(lab.app_url))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class Actor:
    context: OrgContext
    conversation: UUID


def actor(lab: RlsLab, tenant_id: UUID) -> Actor:
    with lab.admin.begin() as db:
        tenant, org, owner, agent = _actor(db, tenant_id)
        db.execute(sa.insert(TenantMember).values(tenant_id=tenant, user_id=owner))
        db.execute(
            sa.insert(OrganizationMember).values(org_id=org, tenant_id=tenant, user_id=owner)
        )
        conversation = uuid4()
        db.execute(
            sa.insert(Conversation).values(
                id=conversation,
                tenant_id=tenant,
                org_id=org,
                user_id=owner,
                agent_id=agent,
                agent_name_snapshot="Agent",
                last_activity_at=datetime.now(UTC),
            )
        )
    return Actor(OrgContext(org, tenant, owner), conversation)


async def enqueue_and_bind(
    db: AsyncSession, owner: Actor, admission: commands.CommandAdmission
) -> UUID:
    queued = await enqueue_input(
        db,
        conversation_id=owner.conversation,
        context=owner.context,
        # Distinct downstream request IDs ensure this test detects a second admission.
        client_request_id=str(uuid4()),
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "hello"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    bound = await commands.bind_command(db, admission, input_id=queued.id, checkpoint_id="base")
    assert bound.input_id == queued.id and bound.run_id is None
    return queued.id


@pytest.mark.parametrize("first_commits", [True, False], ids=["commit-replay", "crash-retry"])
async def test_double_submit_serializes_and_rollback_can_be_retried(
    lab: RlsLab, sessions: async_sessionmaker[AsyncSession], first_commits: bool
) -> None:
    owner = actor(lab, lab.tenant_a)
    context, conversation = owner.context, owner.conversation
    async with (
        tenant_session(context, maker=sessions) as first,
        tenant_session(context, maker=sessions) as second,
    ):
        holder = await first.scalar(sa.text("SELECT pg_backend_pid()"))
        waiter = await second.scalar(sa.text("SELECT pg_backend_pid()"))
        assert holder != waiter
        admission = await commands.find_or_reserve(first, context, conversation, _PUBLIC, _DIGEST)
        assert admission.existing is None
        first_input = await enqueue_and_bind(first, owner, admission)

        async def second_caller() -> UUID:
            other = await commands.find_or_reserve(second, context, conversation, _PUBLIC, _DIGEST)
            if first_commits:
                assert other.existing is not None and other.existing.input_id is not None
                accepted = other.existing.input_id
            else:
                assert other.existing is None
                accepted = await enqueue_and_bind(second, owner, other)
            await second.commit()
            return accepted

        async with asyncio.timeout(20), asyncio.TaskGroup() as workers:
            pending = workers.create_task(second_caller())
            while not await first.scalar(
                sa.text("SELECT :holder = ANY(pg_blocking_pids(:waiter))"),
                {"holder": holder, "waiter": waiter},
            ):
                if pending.done():
                    await pending
                    pytest.fail(
                        "The concurrent caller completed before the writer released its lock"
                    )
                await asyncio.sleep(0.02)
            if first_commits:
                await first.commit()
            else:
                await first.rollback()
        accepted_input = pending.result()
        assert (accepted_input == first_input) is first_commits

    async with tenant_session(context, maker=sessions) as db:
        inputs = await db.scalars(
            sa.select(ConversationRunInput.id).where(
                ConversationRunInput.conversation_id == conversation
            )
        )
        assert inputs.all() == [accepted_input]
        with pytest.raises(ConflictError) as rejected:
            await commands.find_or_reserve(db, context, conversation, _PUBLIC, "2" * 64)
        assert rejected.value.code == "AG_UI_RUN_ID_CONFLICT"
        await db.rollback()
        initial = await commands.load_run_binding(db, context, conversation, _PUBLIC)
        assert initial is not None
        assert (initial.public_run_id, initial.input_id, initial.input_status, initial.run_id) == (
            _PUBLIC,
            accepted_input,
            "pending",
            None,
        )
        assert (initial.projection_version, initial.checkpoint_id) == ("1", "base")
        claimed = await claim_next_input(db, conversation_id=conversation, context=context)
        assert claimed is not None and claimed.input.id == accepted_input
        run_id = claimed.run.id
        await db.commit()

    async with tenant_session(context, maker=sessions) as db:
        claimed_binding = await commands.load_run_binding(db, context, conversation, _PUBLIC)
        assert claimed_binding is not None
        assert (claimed_binding.input_id, claimed_binding.input_status, claimed_binding.run_id) == (
            accepted_input,
            "claimed",
            run_id,
        )
        assert await commands.load_public_run_id(db, context, conversation, run_id) == _PUBLIC


async def test_receipt_scope_target_retention_and_conversation_deletion(
    lab: RlsLab, sessions: async_sessionmaker[AsyncSession]
) -> None:
    owner = actor(lab, lab.tenant_a)
    other = actor(lab, lab.tenant_b)
    context, conversation = owner.context, owner.conversation
    async with tenant_session(context, maker=sessions) as db:
        admission = await commands.find_or_reserve(db, context, conversation, _PUBLIC, _DIGEST)
        queued_id = await enqueue_and_bind(db, owner, admission)
        await db.commit()
        # The app role cannot move the receipt across the RLS company boundary.
        with pytest.raises(DBAPIError) as denied:
            await db.execute(
                sa.update(AgUiCommandReceipt)
                .where(AgUiCommandReceipt.conversation_id == conversation)
                .values(tenant_id=other.context.tenant_id, org_id=other.context.org_id)
            )
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
        await db.rollback()
    async with tenant_session(other.context, maker=sessions) as db:
        assert (
            await db.scalar(
                sa.select(AgUiCommandReceipt.conversation_id).where(
                    AgUiCommandReceipt.conversation_id == conversation
                )
            )
            is None
        )
        assert await commands.load_run_binding(db, other.context, conversation, _PUBLIC) is None
    async with tenant_session(context, maker=sessions) as db:
        # A stray target deletion cannot turn an accepted public run into a new request.
        await db.execute(
            sa.delete(ConversationRunInput).where(ConversationRunInput.id == queued_id)
        )
        with pytest.raises(IntegrityError) as failed:
            await db.commit()
        assert getattr(failed.value.orig, "sqlstate", None) == "23503"
        await db.rollback()
        assert await commands.load_run_binding(db, context, conversation, _PUBLIC) is not None
        await db.execute(sa.delete(Conversation).where(Conversation.id == conversation))
        await db.commit()
        assert (
            await db.scalar(
                sa.select(AgUiCommandReceipt.conversation_id).where(
                    AgUiCommandReceipt.conversation_id == conversation
                )
            )
            is None
        )


async def test_removed_organization_membership_blocks_read_and_replay(
    lab: RlsLab, sessions: async_sessionmaker[AsyncSession]
) -> None:
    owner = actor(lab, lab.tenant_a)
    context, conversation = owner.context, owner.conversation
    async with tenant_session(context, maker=sessions) as db:
        admission = await commands.find_or_reserve(db, context, conversation, _PUBLIC, _DIGEST)
        queued_id = await enqueue_and_bind(db, owner, admission)
        claimed = await claim_next_input(db, conversation_id=conversation, context=context)
        assert claimed is not None and claimed.input.id == queued_id
        run_id = claimed.run.id
        await db.commit()

    with lab.admin.begin() as admin:
        admin.execute(
            sa.update(OrganizationMember)
            .where(
                OrganizationMember.org_id == context.org_id,
                OrganizationMember.user_id == context.user_id,
            )
            .values(status="removed")
        )

    async with tenant_session(context, maker=sessions) as db:
        assert await commands.load_run_binding(db, context, conversation, _PUBLIC) is None
        assert await commands.load_public_run_id(db, context, conversation, run_id) is None
        with pytest.raises(AppError) as rejected:
            await commands.find_or_reserve(db, context, conversation, _PUBLIC, _DIGEST)
        assert rejected.value.status == 404


async def test_workerless_cancel_resolves_only_one_scoped_physical_run(
    lab: RlsLab, sessions: async_sessionmaker[AsyncSession]
) -> None:
    owner = actor(lab, lab.tenant_a)
    other = actor(lab, lab.tenant_b)
    context, conversation = owner.context, owner.conversation
    async with tenant_session(context, maker=sessions) as db:
        admission = await commands.find_or_reserve(db, context, conversation, _PUBLIC, _DIGEST)
        input_id = await enqueue_and_bind(db, owner, admission)
        claimed = await claim_next_input(db, conversation_id=conversation, context=context)
        assert claimed is not None and claimed.input.id == input_id
        run_id, revision = claimed.run.id, claimed.input.revision
        await request_cancel_run(db, claimed.run)
        await db.commit()
    finalized = await finalize_workerless_cancel_before_start(
        session_factory=platform_sessions("conversation_run.queue", maker=sessions),
        protected_run_ids=(),
    )
    assert finalized == [conversation]

    async with tenant_session(other.context, maker=sessions) as db:
        assert await commands.load_run_binding(db, other.context, conversation, _PUBLIC) is None
        assert await commands.load_public_run_id(db, other.context, conversation, run_id) is None
    async with tenant_session(context, maker=sessions) as db:
        queued = await db.get(ConversationRunInput, input_id)
        run = await db.get(ConversationRun, run_id)
        assert queued is not None and run is not None
        assert (queued.status, queued.run_id, queued.revision) == ("canceled", None, revision + 1)
        assert run.status == "canceled" and run.cancellation_acknowledged_at is not None
        terminal = await commands.load_run_binding(db, context, conversation, _PUBLIC)
        assert terminal is not None
        assert (terminal.run_id, terminal.input_id, terminal.revision) == (
            run_id,
            input_id,
            queued.revision,
        )
        assert await commands.load_run_identities(db, context, conversation, [run_id]) == {
            run_id: (_PUBLIC, "base")
        }
        assert run.metadata_json is not None
        copied = ConversationRun(
            conversation_id=conversation,
            user_id=context.user_id,
            org_id=context.org_id,
            tenant_id=context.tenant_id,
            agent_id=run.agent_id,
            source=run.source,
            status="canceled",
            metadata_json=dict(run.metadata_json),
        )
        db.add(copied)
        await db.commit()

    async with tenant_session(context, maker=sessions) as db:
        ambiguous = await commands.load_run_binding(db, context, conversation, _PUBLIC)
        assert ambiguous is not None and ambiguous.run_id is None and ambiguous.input_id == input_id
        # A one-item page must also consider the second match outside the requested page.
        assert await commands.load_public_run_id(db, context, conversation, run_id) is None
        assert await commands.load_public_run_id(db, context, conversation, copied.id) is None
