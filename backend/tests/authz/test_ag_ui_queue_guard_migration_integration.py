"""Upgrade both deployed i51 catalogs without disturbing persisted chat requests."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.config import settings
from app.database import tenant_session
from app.models import Conversation, ConversationRun, ConversationRunInput
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation_run import utc_now_naive
from app.services import ag_ui_commands as commands
from app.services.conversation_run_queue_claim import claim_next_input
from tests.authz.i06_rls_lab import BACKEND, RlsLab, async_url, rls_lab
from tests.authz.test_ag_ui_command_receipt_integration import Actor, actor, enqueue_pending

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
_I39 = "i39_skill_approval_default"
_I51 = "i51_ag_ui_commands"
_I52 = "i52_ag_ui_queue_claim_guard"
_PUBLIC = "keep-this-run-id"
_DIGEST = "7" * 64
_SELECTED = "previously-selected-checkpoint"
_REFUSAL = "Cannot downgrade while selected AG-UI inputs need a queue claim guard"


@pytest.fixture
def lab() -> Iterator[RlsLab]:
    with rls_lab() as disposable:
        yield disposable


@pytest.fixture
def config(lab: RlsLab, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "database_url", async_url(lab.app_url))
    monkeypatch.setattr(settings, "migration_database_url", async_url(lab.admin_url))
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = async_url(lab.admin_url)
    return config


@pytest.fixture
async def sessions(lab: RlsLab) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(async_url(lab.app_url))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def assert_catalog(lab: RlsLab, revision: str, *, guard: bool) -> None:
    with lab.admin.connect() as db:
        assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == revision
        assert db.scalar(sa.text("SELECT to_regclass('ag_ui_command_receipts') IS NOT NULL"))
        assert (
            db.scalar(
                sa.text("SELECT to_regprocedure('moldy_guard_ag_ui_queue_claim()') IS NOT NULL")
            )
            is guard
        )
        count = db.scalar(
            sa.text(
                "SELECT count(*) FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE t.tgrelid = 'conversation_run_inputs'::regclass "
                "AND t.tgname = 'ck_ag_ui_selected_queue_claim' AND NOT t.tgisinternal "
                "AND t.tgenabled = 'O' AND p.proname = 'moldy_guard_ag_ui_queue_claim' "
                "AND NOT p.prosecdef"
            )
        )
        assert count == int(guard)


async def persist_input(db: AsyncSession, owner: Actor, *, selected: bool) -> UUID:
    admission = await commands.find_or_reserve(
        db, owner.context, owner.conversation, _PUBLIC, _DIGEST
    )
    input_id = await enqueue_pending(db, owner)
    await commands.bind_command(db, admission, input_id=input_id)
    if selected:
        await db.execute(
            sa.update(Conversation)
            .where(Conversation.id == owner.conversation)
            .values(active_branch_checkpoint_id=_SELECTED)
        )
    await db.commit()
    return input_id


async def assert_retained(db: AsyncSession, owner: Actor, input_id: UUID, status: str) -> None:
    room = await db.get(Conversation, owner.conversation)
    assert room and room.active_branch_checkpoint_id == _SELECTED
    binding = await commands.load_run_binding(db, owner.context, owner.conversation, _PUBLIC)
    assert binding and (binding.input_id, binding.input_status) == (input_id, status)
    assert binding.public_run_id == _PUBLIC and binding.projection_version == "1"
    input_row = await db.get(ConversationRunInput, input_id)
    assert input_row and input_row.run_id is None and input_row.checkpoint_id is None
    assert input_row.input_payload == {"messages": [{"role": "user", "content": "hello"}]}
    assert not await db.scalar(
        sa.select(ConversationRun.id).where(ConversationRun.conversation_id == owner.conversation)
    )


async def saved_rows(db: AsyncSession, owner: Actor) -> list[dict[str, object]]:
    # Each table must have exactly the original row; include all columns in the comparison.
    result = []
    for table, predicate in (
        (Conversation.__table__, Conversation.id == owner.conversation),
        (
            ConversationRunInput.__table__,
            ConversationRunInput.conversation_id == owner.conversation,
        ),
        (ConversationRun.__table__, ConversationRun.conversation_id == owner.conversation),
        (AgUiCommandReceipt.__table__, AgUiCommandReceipt.conversation_id == owner.conversation),
    ):
        row = (await db.execute(sa.select(table).where(predicate))).mappings().one()
        result.append(dict(row))
    return result


async def claim_without_pin(db: AsyncSession, owner: Actor, input_id: UUID) -> UUID:
    queued = await db.get(ConversationRunInput, input_id)
    assert queued is not None
    run = ConversationRun(
        id=uuid4(),
        conversation_id=owner.conversation,
        agent_id=queued.agent_id,
        user_id=owner.context.user_id,
        tenant_id=owner.context.tenant_id,
        org_id=owner.context.org_id,
        source="chat",
        status="queued",
        is_active=True,
        metadata_json={
            "queue_input_id": str(queued.id),
            "client_request_id": queued.client_request_id,
            "checkpoint_id": None,
        },
    )
    db.add(run)
    await db.flush()
    queued.status, queued.run_id = "claimed", run.id
    queued.claimed_at = utc_now_naive()
    queued.revision += 1
    await db.commit()
    return run.id


@pytest.mark.parametrize("provisional", [False, True], ids=["original-i51", "guarded-i51"])
async def test_upgrade_keeps_selected_pending_receipt(
    lab: RlsLab, config: Config, sessions: async_sessionmaker[AsyncSession], provisional: bool
) -> None:
    await asyncio.to_thread(command.downgrade, config, _I39)
    await asyncio.to_thread(command.upgrade, config, _I51)
    assert_catalog(lab, _I51, guard=False)
    if provisional:
        # The provisional schema had the same live guard but its stamp still pointed at i51.
        await asyncio.to_thread(command.upgrade, config, _I52)
        await asyncio.to_thread(command.stamp, config, _I51)
    assert_catalog(lab, _I51, guard=provisional)
    owner = actor(lab, lab.tenant_a)
    async with tenant_session(owner.context, maker=sessions) as db:
        assert await db.scalar(
            sa.text(
                "SELECT NOT (rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname=current_user"
            )
        )
        input_id = await persist_input(db, owner, selected=True)
        await assert_retained(db, owner, input_id, "pending")

    await asyncio.to_thread(command.upgrade, config, _I52)
    assert_catalog(lab, _I52, guard=True)
    async with tenant_session(owner.context, maker=sessions) as db:
        await assert_retained(db, owner, input_id, "pending")
        with pytest.raises(IntegrityError) as rejected:
            await claim_without_pin(db, owner, input_id)
        assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
        await db.rollback()

    with pytest.raises(RuntimeError, match=_REFUSAL):
        await asyncio.to_thread(command.downgrade, config, _I51)
    assert_catalog(lab, _I52, guard=True)
    async with tenant_session(owner.context, maker=sessions) as db:
        await assert_retained(db, owner, input_id, "pending")
        await db.execute(
            sa.update(ConversationRunInput)
            .where(ConversationRunInput.id == input_id)
            .values(status="canceled")
        )
        await db.commit()

    await asyncio.to_thread(command.downgrade, config, _I51)
    assert_catalog(lab, _I51, guard=False)
    await asyncio.to_thread(command.upgrade, config, _I52)
    assert_catalog(lab, _I52, guard=True)
    async with tenant_session(owner.context, maker=sessions) as db:
        await assert_retained(db, owner, input_id, "canceled")


async def test_upgrade_fails_only_unstarted_ambiguous_claim_and_preserves_worker_ownership(
    lab: RlsLab, config: Config, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await asyncio.to_thread(command.downgrade, config, _I51)
    owner = actor(lab, lab.tenant_a)
    async with tenant_session(owner.context, maker=sessions) as db:
        input_id = await persist_input(db, owner, selected=True)
        run_id = await claim_without_pin(db, owner, input_id)
        before = await saved_rows(db, owner)

    # A current worker can attest the default branch at claim time on the original schema.
    default = actor(lab, lab.tenant_b)
    async with tenant_session(default.context, maker=sessions) as db:
        default_input = await persist_input(db, default, selected=False)
        claim = await claim_next_input(
            db, conversation_id=default.conversation, context=default.context
        )
        assert claim and claim.input.id == default_input and claim.input.checkpoint_id is None
        assert claim.run.metadata_json and claim.run.metadata_json["ag_ui_default_branch_at_claim"]
        await db.execute(
            sa.update(Conversation)
            .where(Conversation.id == default.conversation)
            .values(active_branch_checkpoint_id=_SELECTED)
        )
        await db.commit()
        default_before = await saved_rows(db, default)

    active = actor(lab, lab.tenant_a)
    async with tenant_session(active.context, maker=sessions) as worker:
        active_input = await persist_input(worker, active, selected=True)
        active_run = await claim_without_pin(worker, active, active_input)
        now = utc_now_naive()
        await worker.execute(
            sa.update(ConversationRun)
            .where(ConversationRun.id == active_run)
            .values(
                status="running",
                worker_instance_id="current-worker",
                owner_generation=1,
                owner_token=uuid4(),
                started_at=now,
                heartbeat_at=now,
            )
        )
        active_before = await saved_rows(worker, active)
        # The worker owns the row before the migration can obtain its lock. Observe PostgreSQL's
        # actual lock wait before letting the worker commit its new execution state.
        upgrading = asyncio.create_task(asyncio.to_thread(command.upgrade, config, _I52))
        try:
            async with asyncio.timeout(30):
                while not await worker.scalar(
                    sa.text(
                        "SELECT EXISTS (SELECT FROM pg_locks WHERE NOT granted "
                        "AND pg_backend_pid() = ANY(pg_blocking_pids(pid)))"
                    )
                ):
                    if upgrading.done():
                        await upgrading
                        pytest.fail("The migration completed before the worker released its lock")
                    await asyncio.sleep(0.02)
        finally:
            await worker.commit()
            await upgrading

    assert_catalog(lab, _I52, guard=True)
    async with tenant_session(active.context, maker=sessions) as db:
        assert await saved_rows(db, active) == active_before
    async with tenant_session(default.context, maker=sessions) as db:
        assert await saved_rows(db, default) == default_before
    async with tenant_session(owner.context, maker=sessions) as db:
        after = await saved_rows(db, owner)
        old_room, old_input, old_run, old_receipt = before
        room, queued, failed, receipt = after
        assert (room, receipt) == (old_room, old_receipt)
        revision = old_input["revision"]
        assert isinstance(revision, int)
        assert queued == {
            **old_input,
            "status": "failed",
            "revision": revision + 1,
            "updated_at": queued["updated_at"],
        }
        assert failed["completed_at"] and failed["error_message"]
        assert failed == {
            **old_run,
            "status": "failed",
            "is_active": False,
            "error_code": "queue_branch_unpinned",
            "error_message": failed["error_message"],
            "updated_at": failed["updated_at"],
            "completed_at": failed["completed_at"],
        }
        bound = await commands.load_run_binding(db, owner.context, owner.conversation, _PUBLIC)
        assert bound and (bound.input_status, bound.run_id, bound.input_id) == (
            "failed",
            run_id,
            input_id,
        )

    await asyncio.to_thread(command.downgrade, config, _I51)
    assert_catalog(lab, _I51, guard=False)
    await asyncio.to_thread(command.upgrade, config, _I52)
    async with tenant_session(owner.context, maker=sessions) as db:
        assert await saved_rows(db, owner) == after


async def test_upgrade_preserves_existing_execution_evidence_and_unmatched_claims(
    lab: RlsLab, config: Config, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await asyncio.to_thread(command.downgrade, config, _I51)
    now = utc_now_naive()
    updates = (
        {"runtime_materialized_at": now, "runtime_materialization_sha256": "0" * 64},
        {"cancel_requested_at": now},
        {"status": "completed", "completed_at": now, "is_active": False},
        {"metadata_json": {"queue_input_id": str(uuid4()), "client_request_id": "other"}},
        {"last_event_id": "previous-turn/1"},
    )
    snapshots = []
    for values in updates:
        owner = actor(lab, lab.tenant_a)
        async with tenant_session(owner.context, maker=sessions) as db:
            input_id = await persist_input(db, owner, selected=True)
            run_id = await claim_without_pin(db, owner, input_id)
            await db.execute(
                sa.update(ConversationRun).where(ConversationRun.id == run_id).values(**values)
            )
            await db.commit()
            snapshots.append((owner, await saved_rows(db, owner)))

    await asyncio.to_thread(command.upgrade, config, _I52)
    with pytest.raises(RuntimeError, match=_REFUSAL):
        await asyncio.to_thread(command.downgrade, config, _I51)
    assert_catalog(lab, _I52, guard=True)
    for owner, before in snapshots:
        async with tenant_session(owner.context, maker=sessions) as db:
            assert await saved_rows(db, owner) == before
