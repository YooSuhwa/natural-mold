"""The claimed worker checks current can_run before reading any saved user input."""

from typing import Literal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.conversation_agent_checks import require_agent_run
from app.authz.run_decisions import fresh_run_decision_scope
from app.authz.runtime import legacy_service
from app.authz.service import AuthzService
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_service as queue
from app.services import conversation_run_queue_worker as worker
from tests.authz.d04_support import Unavailable
from tests.authz.execution_gateway_testkit import seed_graph
from tests.authz.f05_support import enforce
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_private_queue_worker_read_order import private_selects
from tests.authz.test_shared_agent_run_conversation_lock import _shared


@pytest.mark.parametrize("owner", ["private", "shared"])
@pytest.mark.parametrize("admission", ["current", "revoked", "outage"])
async def test_worker_refreshes_previous_request_decision(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    owner: Literal["private", "shared"],
    admission: Literal["current", "revoked", "outage"],
) -> None:
    if owner == "shared":
        shared = await _shared(db)
        context, agent_id, conversation_id = shared.context, shared.agent_id, shared.conversation_id
    else:
        graph = await seed_graph(db, conversations=1)
        context, agent_id, conversation_id = (
            graph.context,
            graph.agent_id,
            graph.conversation_ids[0],
        )
    grant = (f"user:{context.user_id}", "can_run", f"agent:{agent_id}")
    monkeypatch.setattr(runtime, "_current", enforce(grant))
    queued = await queue.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=context,
        client_request_id="real-grant",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "private fixture"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    claimed = await queue.claim_next_input(db, conversation_id=conversation_id, context=context)
    assert claimed is not None
    await db.commit()
    prepare, start = AsyncMock(), AsyncMock()
    monkeypatch.setattr(worker, "resolve_agent_context", prepare)
    monkeypatch.setattr(worker, "start_conversation_run", start)
    with fresh_run_decision_scope():
        await require_agent_run(context, agent_id)
        if admission == "revoked":
            monkeypatch.setattr(runtime, "_current", enforce())
        elif admission == "outage":
            monkeypatch.setattr(runtime, "_current", AuthzService(Unavailable(), mode="enforce"))
        with queue_resource_sql(db) as statements:
            await worker.launch_claimed_input(queued.id)
    if admission != "current":
        prepare.assert_not_called()
        start.assert_not_called()
        assert private_selects(statements) == []
        refreshed = await db.get(ConversationRunInput, queued.id, populate_existing=True)
        assert refreshed is not None
        if admission == "revoked":
            assert refreshed.status == "failed"
            return
        assert refreshed.status == "claimed" and refreshed.run_id == claimed.run.id
        monkeypatch.setattr(runtime, "_current", enforce(grant))
        await worker.launch_claimed_input(queued.id)
    prepare.assert_awaited_once()
    start.assert_awaited_once()
    assert start.await_args is not None
    assert start.await_args.kwargs["run_id"] == claimed.run.id


async def test_worker_metadata_keeps_shared_agent_private_in_legacy(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = await _shared(db)
    ctx = shared.context
    monkeypatch.setattr(
        runtime, "_current", enforce((f"user:{ctx.user_id}", "can_run", f"agent:{shared.agent_id}"))
    )
    queued = await queue.enqueue_input(
        db,
        conversation_id=shared.conversation_id,
        context=ctx,
        client_request_id="previous",
        source="chat",
        input_payload={"messages": []},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    assert await queue.claim_next_input(db, conversation_id=shared.conversation_id, context=ctx)
    await db.commit()
    monkeypatch.setattr(runtime, "_current", legacy_service())
    with queue_resource_sql(db) as statements:
        assert await worker.launch_claimed_input(queued.id) is None
    assert private_selects(statements) == []
