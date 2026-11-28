"""A durable rollover continues the originating branch across claims and reconnects."""

from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime as authz_runtime
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.run_decisions import fresh_run_decision_scope
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.conversation_run_steer import ConversationRunSteer
from app.services import conversation_run_queue_service, conversation_run_service
from app.services.conversation_run_queue_persistence import QueueInputSource
from app.services.conversation_run_queue_worker import dispatch_next_for_conversation
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.test_conversation_route_authz import _service
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent

OWNER = unit_context(TEST_USER_ID)


@pytest.fixture
def saver(monkeypatch: pytest.MonkeyPatch) -> InMemorySaver:
    memory = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: memory)
    return memory


async def _checkpoint(saver: InMemorySaver, run: ConversationRun, parent: str | None = None) -> str:
    config = run_configurable(str(run.conversation_id), run.id, OWNER.tenant_id)
    config["checkpoint_ns"] = ""
    if parent is not None:
        config["checkpoint_id"] = parent
    cp = empty_checkpoint()
    await saver.aput({"configurable": config}, cp, {}, {})
    return cp["id"]


async def _source(db: AsyncSession, *, never_started: bool = False) -> ConversationRun:
    conversation_id = await seed_conversation_with_agent()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None and conversation.agent_id is not None
    run = await conversation_run_service.create_run(
        db,
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        context=OWNER,
        source="chat",
        input_preview=None,
    )
    if never_started:
        await conversation_run_service.transition_run(db, run, "failed", error_code="start_failed")
    else:
        await conversation_run_service.transition_run(db, run, "running", worker_instance_id="test")
        await conversation_run_service.transition_run(db, run, "completed")
    await db.commit()
    return run


async def _input(
    db: AsyncSession, conversation_id: UUID, request_id: str, *, source: QueueInputSource = "chat"
) -> ConversationRunInput:
    return await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=OWNER,
        client_request_id=request_id,
        source=source,
        input_payload={"messages": [{"role": "user", "content": "추가 조사"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )


async def _rollover(db: AsyncSession, run: ConversationRun, sequence: int) -> ConversationRunSteer:
    steer_id = uuid4()
    queued = await _input(db, run.conversation_id, f"steer:{steer_id}", source="steer")
    steer = ConversationRunSteer(
        id=steer_id,
        conversation_id=run.conversation_id,
        run_id=run.id,
        user_id=OWNER.user_id,
        org_id=OWNER.org_id,
        tenant_id=OWNER.tenant_id,
        client_request_id=uuid4(),
        text="추가 조사",
        sequence=sequence,
        status="queued",
        queued_input_id=queued.id,
    )
    db.add(steer)
    await db.commit()
    return steer


async def test_two_rollovers_continue_prior_results(db: AsyncSession, saver: InMemorySaver) -> None:
    source = await _source(db)
    origin = await _checkpoint(saver, source)
    await _rollover(db, source, 1)
    await _rollover(db, source, 2)
    first = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=source.conversation_id, context=OWNER
    )
    assert first is not None
    assert first.input.checkpoint_id == origin
    await conversation_run_service.transition_run(
        db, first.run, "running", worker_instance_id="test"
    )
    progressed = origin
    for _ in range(36):  # Span a checkpoint history page.
        progressed = await _checkpoint(saver, first.run, progressed)
    await conversation_run_service.transition_run(db, first.run, "completed")
    await db.commit()
    second = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=source.conversation_id, context=OWNER
    )
    assert second is not None
    assert second.input.checkpoint_id == progressed
    assert second.run.id != first.run.id


async def test_selected_sibling_holds_directive_durably(
    db: AsyncSession, saver: InMemorySaver
) -> None:
    source = await _source(db)
    initial = await _checkpoint(saver, source)
    await _checkpoint(saver, source, initial)
    steer = await _rollover(db, source, 1)
    queued_id = steer.queued_input_id
    assert queued_id is not None

    conversation = await db.get(Conversation, source.conversation_id)
    assert conversation is not None
    sibling = ConversationRun(id=uuid4(), conversation_id=conversation.id)
    conversation.active_branch_checkpoint_id = await _checkpoint(saver, sibling, initial)
    await db.commit()
    assert await dispatch_next_for_conversation(conversation.id) is None

    async with TestSession() as fresh:
        held = await fresh.get(ConversationRunSteer, steer.id)
        dropped = await fresh.get(ConversationRunInput, queued_id)
        assert held is not None and held.status == "needs_review" and held.queued_input_id is None
        assert dropped is not None and dropped.status == "canceled" and dropped.run_id is None
        assert (
            await fresh.scalar(
                select(func.count())
                .select_from(ConversationRun)
                .where(ConversationRun.conversation_id == conversation.id)
            )
            == 1
        )


async def test_selected_older_branch_uses_its_latest_leaf(
    db: AsyncSession, saver: InMemorySaver
) -> None:
    source = await _source(db)
    root = await _checkpoint(saver, source)
    original = await _checkpoint(saver, source, root)
    later = ConversationRun(id=uuid4(), conversation_id=source.conversation_id)
    correct_tip = await _checkpoint(saver, later, original)
    await _checkpoint(saver, later, root)  # Newest globally, outside the selected lineage.
    conversation = await db.get(Conversation, source.conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = original
    await db.commit()
    await _rollover(db, source, 1)

    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation.id, context=OWNER
    )
    assert claimed is not None and claimed.input.checkpoint_id == correct_tip


async def test_prefix_on_an_ordinary_queue_input_still_dispatches(db: AsyncSession) -> None:
    source = await _source(db)
    ordinary = await _input(db, source.conversation_id, "steer:user-picked-request-id")
    await db.commit()
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=source.conversation_id, context=OWNER
    )
    assert claimed is not None and claimed.input.id == ordinary.id
    assert claimed.input.checkpoint_id is None


@pytest.mark.parametrize("foreign", [False, True])
async def test_captured_admission_selection_must_belong_to_the_continuation(
    db: AsyncSession, saver: InMemorySaver, foreign: bool
) -> None:
    source = await _source(db)
    prior = ConversationRun(id=uuid4(), conversation_id=source.conversation_id)
    root = await _checkpoint(saver, prior)
    if foreign:
        config = run_configurable(str(source.conversation_id), prior.id, uuid4())
        config.update(checkpoint_id=root, checkpoint_ns="")
        cp = empty_checkpoint()
        await saver.aput({"configurable": config}, cp, {}, {})
        old_selection = cp["id"]
        await _checkpoint(saver, source, old_selection)
    else:
        old_selection = await _checkpoint(saver, prior, root)
        await _checkpoint(saver, source, root)
    steer = await _rollover(db, source, 1)
    steer.source_branch_checkpoint_id = old_selection
    await db.commit()

    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=source.conversation_id, context=OWNER
    )
    assert claimed is None
    assert steer.status == "needs_review"


async def test_orphaned_server_rollover_never_executes(db: AsyncSession) -> None:
    source = await _source(db)
    orphaned = await _input(db, source.conversation_id, "steer:former-row", source="steer")
    await db.commit()
    assert await dispatch_next_for_conversation(source.conversation_id) is None
    await db.refresh(orphaned)
    assert orphaned.status == "canceled" and orphaned.run_id is None


async def test_verified_genesis_can_receive_a_never_started_runs_directive(
    db: AsyncSession, saver: InMemorySaver
) -> None:
    source = await _source(db, never_started=True)
    await _rollover(db, source, 1)
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=source.conversation_id, context=OWNER
    )
    assert claimed is not None and claimed.input.checkpoint_id is None


async def test_a_missing_ancestor_id_in_a_parent_link_is_not_branch_proof(
    db: AsyncSession, saver: InMemorySaver
) -> None:
    source = await _source(db)
    missing = str(uuid4())
    await _checkpoint(saver, source, missing)
    steer = await _rollover(db, source, 1)
    steer.source_branch_checkpoint_id = missing
    await db.commit()

    assert (
        await conversation_run_queue_service.claim_next_input(
            db, conversation_id=source.conversation_id, context=OWNER
        )
        is None
    )
    assert steer.status == "needs_review"


async def test_dispatch_rechecks_a_revoked_grant_after_the_parent_accepted_it(
    db: AsyncSession, saver: InMemorySaver, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await _source(db)
    assert source.agent_id is not None
    await _checkpoint(saver, source)
    steer = await _rollover(db, source, 1)
    grant = (f"user:{OWNER.user_id}", "can_run", f"agent:{source.agent_id}")
    monkeypatch.setattr(authz_runtime, "_current", _service("enforce", {grant}))
    with fresh_run_decision_scope() as parent:
        await require_agent_run(OWNER, source.agent_id)
        assert parent.answers
        monkeypatch.setattr(authz_runtime, "_current", _service("enforce"))
        assert await dispatch_next_for_conversation(source.conversation_id) is None

    bound = await db.get(ConversationRunInput, steer.queued_input_id)
    assert bound is not None and bound.status == "pending" and bound.run_id is None
    async with TestSession() as fresh:
        conversation = await fresh.get(Conversation, source.conversation_id)
        assert conversation is not None and conversation.queue_paused
