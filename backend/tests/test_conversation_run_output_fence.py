from __future__ import annotations

import uuid

import pytest

from app.authz.conversation_scope import require_conversation_agent_id
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service, conversation_stream_service, trace_storage
from app.services.conversation_run_delivery import (
    _publish_error,
    _publish_message_end,
    _publish_stale,
)
from app.services.conversation_run_fence import (
    RunFence,
    RunOwnershipLost,
    claim_run_owner,
    run_fence_scope,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent


async def _owned_run() -> RunFence:
    conversation_id = await seed_conversation_with_agent()
    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation.id,
            agent_id=require_conversation_agent_id(conversation),
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="fence",
        )
        await conversation_run_service.transition_run(db, run, "running", worker_instance_id="one")
        fence = claim_run_owner(run, "one")
        await db.commit()
        return fence


@pytest.mark.asyncio
async def test_replaced_worker_cannot_persist_publish_or_finalize() -> None:
    original = await _owned_run()
    run_id = str(original.run_id)
    ctx = conversation_stream_service.prepare_stream_context(
        original.conversation_id, run_id=run_id, tenant=original.tenant
    )
    with run_fence_scope(original):
        await ctx.persist_cb([{"id": "ack", "event": "status", "data": {}}])
    async with TestSession() as db:
        run = await db.get(ConversationRun, original.run_id, with_for_update=True)
        assert run is not None
        successor = claim_run_owner(run, "two")
        await db.commit()
    with run_fence_scope(original):
        with pytest.raises(RunOwnershipLost):
            await ctx.persist_cb([{"id": "late", "event": "content_delta", "data": {}}])
        with pytest.raises(RunOwnershipLost):
            await _publish_error(ctx, "late error")
        with pytest.raises(RunOwnershipLost):
            await _publish_message_end(ctx, status="canceled")
        with pytest.raises(RunOwnershipLost):
            await _publish_stale(ctx, reason="worker_shutdown")
        with pytest.raises(RunOwnershipLost):
            await conversation_stream_service.finalize_trace(
                original.conversation_id, run_id, [], [], [], success=False, tenant=original.tenant
            )
    assert ctx.broker.last_event_id is None
    with run_fence_scope(successor):
        await ctx.persist_cb([{"id": "second", "event": "status", "data": {}}])
        with pytest.raises(RunOwnershipLost):
            await conversation_stream_service.finalize_trace(
                uuid.uuid4(), run_id, [], [], [], success=False, tenant=successor.tenant
            )
    async with TestSession() as db:
        record = await trace_storage.get_trace_by_msg_id(db, run_id)
        assert record is not None and record.status == "streaming"
        assert [e["id"] for e in record.events] == ["ack", "second"]


@pytest.mark.asyncio
async def test_cancel_intent_blocks_content_but_owner_persists_terminal_marker() -> None:
    owner = await _owned_run()
    ctx = conversation_stream_service.prepare_stream_context(
        owner.conversation_id, run_id=str(owner.run_id), tenant=owner.tenant
    )
    async with TestSession() as db:
        run = await db.get(ConversationRun, owner.run_id, with_for_update=True)
        assert run is not None
        await conversation_run_service.request_cancel_run(db, run)
        await db.commit()
    with run_fence_scope(owner):
        with pytest.raises(RunOwnershipLost):
            await ctx.persist_cb([{"id": "late", "event": "content_delta", "data": {}}])
        await _publish_message_end(ctx, status="canceled")
    assert ctx.broker.last_event_id == f"{owner.run_id}-canceled"
    async with TestSession() as db:
        record = await trace_storage.get_trace_by_msg_id(db, str(owner.run_id))
        assert record is not None
        assert [event["id"] for event in record.events] == [ctx.broker.last_event_id]


@pytest.mark.asyncio
async def test_only_committed_terminal_decision_can_finalize_fenced_output() -> None:
    fence = await _owned_run()
    run_id = str(fence.run_id)
    ctx = conversation_stream_service.prepare_stream_context(
        fence.conversation_id, run_id=run_id, tenant=fence.tenant
    )
    with run_fence_scope(fence):
        await ctx.persist_cb([{"id": "first", "event": "status", "data": {}}])

        async def finalize(status: str) -> None:
            await conversation_stream_service.finalize_trace(
                fence.conversation_id,
                run_id,
                [],
                [],
                [],
                success=status == "completed",
                run_status=status,
                tenant=fence.tenant,
            )

        with pytest.raises(RunOwnershipLost):
            await finalize("completed")
        async with TestSession() as db:
            run = await db.get(ConversationRun, fence.run_id, with_for_update=True)
            assert run is not None
            await conversation_run_service.transition_run(db, run, "stale")
            await db.commit()
        with pytest.raises(RunOwnershipLost):
            await finalize("completed")
        await finalize("stale")
    async with TestSession() as db:
        record = await trace_storage.get_trace_by_msg_id(db, run_id)
        assert record is not None and record.status == "failed"
