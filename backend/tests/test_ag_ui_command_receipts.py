"""AG-UI public run IDs stay bound to the accepted, currently visible execution."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import ExecutionProofError
from app.authz.org_context import OrgContext
from app.database import Base
from app.dependencies import CurrentUser
from app.exceptions import AppError, ConflictError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.independent_chat_job import IndependentChatJob
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services import ag_ui_commands as commands
from app.services import conversation_run_queue_service as queue
from app.services.platform.tenant_data_scope import REDACTED
from app.services.platform.tenant_export_redaction import clean_row
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.authz.unit_scope_fixture import ensure_unit_scope, unit_context
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio

PUBLIC_ID = "대화/turn?client=a#1"
DIGEST = sha256(b"turn one").hexdigest()
OWNER = unit_context(TEST_USER_ID)


async def _run(
    db: AsyncSession, conversation_id: UUID, agent_id: UUID, context: OrgContext
) -> ConversationRun:
    run = ConversationRun(
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=context.user_id,
        org_id=context.org_id,
        tenant_id=context.tenant_id,
        source="chat",
        run_source="CHAT",
        invoker_type="user",
        status="completed",
        is_active=False,
    )
    db.add(run)
    await db.flush()
    return run


async def _enqueue_turn(db: AsyncSession, conversation_id: UUID) -> ConversationRunInput:
    return await queue.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=OWNER,
        client_request_id="request-for-turn",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "Queue me"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )


async def test_retry_reuses_pinned_receipt_and_conflicts_on_changed_input(db: AsyncSession) -> None:
    conversation_id, agent_id = await seed_artifact_conversation()
    first = await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    assert first.existing is None
    run = await _run(db, conversation_id, agent_id, OWNER)
    binding = await commands.bind_command(db, first, run_id=run.id, checkpoint_id="ckpt-before-run")
    await db.commit()

    retry = await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    assert retry.existing == binding
    assert binding.public_run_id == PUBLIC_ID
    assert binding.run_id == run.id
    assert binding.projection_version == "1"
    assert binding.checkpoint_id == "ckpt-before-run"
    await db.commit()
    with pytest.raises(ConflictError) as changed:
        await commands.find_or_reserve(
            db, OWNER, conversation_id, PUBLIC_ID, sha256(b"turn two").hexdigest()
        )
    assert (changed.value.status, changed.value.code) == (409, "AG_UI_RUN_ID_CONFLICT")
    assert await db.scalar(select(func.count()).select_from(AgUiCommandReceipt)) == 1
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 1


async def test_committed_empty_reservation_cannot_bind_in_another_transaction(
    db: AsyncSession,
) -> None:
    conversation_id, agent_id = await seed_artifact_conversation()
    run = await _run(db, conversation_id, agent_id, OWNER)
    admission = await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    await db.commit()
    with pytest.raises((ExecutionProofError, ValueError)):
        await commands.bind_command(db, admission, run_id=run.id)
    assert await db.scalar(select(func.count()).select_from(AgUiCommandReceipt)) == 0
    assert (
        await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    ).existing is None


async def test_queue_claim_updates_read_only_binding_and_reverse_lookup(db: AsyncSession) -> None:
    conversation_id, _ = await seed_artifact_conversation()
    other_id, other_agent = await seed_artifact_conversation()
    other_run = await _run(db, other_id, other_agent, OWNER)
    await db.commit()
    admission = await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    queued = await _enqueue_turn(db, conversation_id)
    pending = await commands.bind_command(db, admission, input_id=queued.id)
    await db.commit()
    assert pending.input_id == queued.id and pending.run_id is None
    assert (pending.input_status, pending.position, pending.revision) == ("pending", 1, 1)

    claimed = await queue.claim_next_input(db, conversation_id=conversation_id, context=OWNER)
    assert claimed is not None
    await db.commit()
    run_id = claimed.run.id
    unrelated = User(email="pending-agui@example.invalid", name="Unflushed")
    db.add(unrelated)

    current = await commands.load_run_binding(db, OWNER, conversation_id, PUBLIC_ID)
    assert current is not None and current.run_id == run_id
    assert current.input_id == queued.id and current.input_status == "claimed"
    assert current.revision == claimed.input.revision == 2
    assert await commands.load_public_run_id(db, OWNER, conversation_id, run_id) == PUBLIC_ID
    assert await commands.load_public_run_ids(db, OWNER, conversation_id, [uuid4(), run_id]) == {
        run_id: PUBLIC_ID
    }
    assert unrelated in db.new

    # Even if an old queue row points across conversations, the receipt cannot expose that run.
    db.expunge(unrelated)
    claimed.input.run_id = other_run.id
    await db.commit()
    assert await commands.load_run_binding(db, OWNER, conversation_id, PUBLIC_ID) is None
    assert (
        await commands.load_public_run_ids(db, OWNER, conversation_id, [run_id, other_run.id]) == {}
    )


async def test_public_id_cannot_shadow_authorized_legacy_run(db: AsyncSession) -> None:
    conversation_id, agent_id = await seed_artifact_conversation()
    foreign_id, foreign_agent = await seed_artifact_conversation()
    legacy = await _run(db, conversation_id, agent_id, OWNER)
    foreign = await _run(db, foreign_id, foreign_agent, OWNER)
    await db.commit()

    with pytest.raises(ConflictError) as collision:
        await commands.find_or_reserve(db, OWNER, conversation_id, str(legacy.id), DIGEST)
    assert collision.value.status == 409
    unrelated = await commands.find_or_reserve(db, OWNER, conversation_id, str(foreign.id), DIGEST)
    assert unrelated.existing is None


@pytest.mark.parametrize("first_target", ["queued", "run"])
async def test_claimed_input_and_physical_run_cannot_receive_two_public_ids(
    db: AsyncSession, first_target: str
) -> None:
    conversation_id, _ = await seed_artifact_conversation()
    queued = await _enqueue_turn(db, conversation_id)
    await db.commit()
    claimed = await queue.claim_next_input(db, conversation_id=conversation_id, context=OWNER)
    assert claimed is not None
    await db.commit()
    first = await commands.find_or_reserve(db, OWNER, conversation_id, "first", DIGEST)
    queue_first = first_target == "queued"
    await commands.bind_command(
        db,
        first,
        input_id=queued.id if queue_first else None,
        run_id=None if queue_first else claimed.run.id,
    )
    await db.commit()
    second = await commands.find_or_reserve(db, OWNER, conversation_id, "second", DIGEST)
    with pytest.raises(ConflictError) as collision:
        await commands.bind_command(
            db,
            second,
            input_id=None if queue_first else queued.id,
            run_id=claimed.run.id if queue_first else None,
        )
    assert collision.value.status == 409
    assert await commands.load_run_binding(db, OWNER, conversation_id, "second") is None
    assert await commands.load_public_run_id(db, OWNER, conversation_id, claimed.run.id) == "first"


@pytest.mark.parametrize("reason", ["another_conversation", "another_owner"])
async def test_bind_verifies_target_conversation_and_owner(db: AsyncSession, reason: str) -> None:
    conversation_id, agent_id = await seed_artifact_conversation()
    if reason == "another_conversation":
        foreign_id, foreign_agent = await seed_artifact_conversation()
        target = await _run(db, foreign_id, foreign_agent, OWNER)
    else:
        other = await ensure_unit_scope(
            db, CurrentUser(id=uuid4(), email="other-agui@example.invalid", name="Other owner")
        )
        target = await _run(db, conversation_id, agent_id, other)
    await db.commit()
    admission = await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    with pytest.raises(AppError) as denied:
        await commands.bind_command(db, admission, run_id=target.id)
    assert denied.value.status == 404
    assert await db.scalar(select(func.count()).select_from(AgUiCommandReceipt)) == 0


async def test_retry_and_mapping_require_current_private_conversation_access(
    db: AsyncSession,
) -> None:
    conversation_id, agent_id = await seed_artifact_conversation()
    other = await ensure_unit_scope(
        db, CurrentUser(id=uuid4(), email="other-reader@example.invalid", name="Other member")
    )
    admission = await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    run = await _run(db, conversation_id, agent_id, OWNER)
    await commands.bind_command(db, admission, run_id=run.id)
    await db.commit()

    member = await db.get(OrganizationMember, (OWNER.org_id, OWNER.user_id))
    assert member is not None
    await db.delete(member)
    await db.commit()
    for context in (OWNER, other):
        assert await commands.load_run_binding(db, context, conversation_id, PUBLIC_ID) is None
        assert await commands.load_public_run_id(db, context, conversation_id, run.id) is None
        assert await commands.load_public_run_ids(db, context, conversation_id, [run.id]) == {}
        with pytest.raises(AppError) as denied:
            await commands.find_or_reserve(db, context, conversation_id, PUBLIC_ID, DIGEST)
        assert denied.value.status == 404


async def test_lookup_obeys_current_independent_job_lineage(
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
) -> None:
    context, _, initial, job = delegation
    conversation_id = job.child_conversation_id
    stray = await _run(db, conversation_id, job.child_agent_id, context)
    hidden_legacy = await _run(db, conversation_id, job.child_agent_id, context)
    for public_id, run in (("initial", initial), ("stray", stray)):
        db.add(
            AgUiCommandReceipt(
                tenant_id=context.tenant_id,
                org_id=context.org_id,
                user_id=context.user_id,
                conversation_id=conversation_id,
                public_run_id=public_id,
                run_id=run.id,
                request_digest=DIGEST,
            )
        )
    await db.commit()

    assert await commands.load_public_run_ids(
        db, context, conversation_id, [initial.id, stray.id]
    ) == {initial.id: "initial"}
    assert await commands.load_run_binding(db, context, conversation_id, "stray") is None
    assert await commands.load_public_run_id(db, context, conversation_id, stray.id) is None
    for body_digest in (DIGEST, sha256(b"different input").hexdigest()):
        with pytest.raises(AppError) as denied:
            await commands.find_or_reserve(db, context, conversation_id, "stray", body_digest)
        assert denied.value.status == 404
    admission = await commands.find_or_reserve(
        db, context, conversation_id, str(hidden_legacy.id), DIGEST
    )
    assert admission.existing is None
    edge = await db.get(AgentSubAgentLink, (job.parent_agent_id, job.child_agent_id))
    assert edge is not None
    await db.delete(edge)
    await db.commit()
    assert await commands.load_run_binding(db, context, conversation_id, "initial") is None
    assert await commands.load_public_run_ids(db, context, conversation_id, [initial.id]) == {}


async def test_delivery_envelope_is_private_on_read_and_tenant_export(db: AsyncSession) -> None:
    conversation_id, agent_id = await seed_artifact_conversation()
    admission = await commands.find_or_reserve(db, OWNER, conversation_id, PUBLIC_ID, DIGEST)
    run = await _run(db, conversation_id, agent_id, OWNER)
    await commands.bind_command(db, admission, run_id=run.id, delivery_encrypted="private-envelope")
    await db.commit()
    db.expunge_all()

    public = await commands.load_run_binding(db, OWNER, conversation_id, PUBLIC_ID)
    assert public is not None and "delivery_encrypted" not in asdict(public)
    receipt = await db.get(AgUiCommandReceipt, (conversation_id, PUBLIC_ID))
    assert receipt is not None and "delivery_encrypted" in inspect(receipt).unloaded
    with pytest.raises(InvalidRequestError):
        _ = receipt.delivery_encrypted
    table = Base.metadata.tables[AgUiCommandReceipt.__tablename__]
    stored = (await db.execute(select(table))).mappings().one()
    assert stored["delivery_encrypted"] == "private-envelope"
    assert clean_row(table, dict(stored))["delivery_encrypted"] == REDACTED
