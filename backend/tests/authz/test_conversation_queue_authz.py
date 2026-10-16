"""D-01: queue dispatch re-checks agent#can_run (plan 9.3)."""

from __future__ import annotations

import pytest

from app.authz.service import ShadowSample, Verdict
from app.services import conversation_run_queue_service
from tests.authz.test_conversation_route_authz import _service, install
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent
from tests.test_conversation_run_queue_service import _enqueue

__all__ = ["install"]


@pytest.mark.asyncio
async def test_queue_claim_rechecks_agent_can_run(db, install) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await _enqueue(db, conversation_id, "authz-claim", "claim me")
    await db.commit()
    context = unit_context(TEST_USER_ID)
    samples: list[ShadowSample] = []
    install(_service("enforce", samples=samples))
    denied = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=context
    )
    assert denied is None
    await db.refresh(queued)
    assert queued.status == "pending"  # stays queued; nothing launched
    install(_service("shadow", samples=samples))  # legacy answer, one sample
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=context
    )
    assert claimed is not None and claimed.input.id == queued.id
    assert [(s.permission, s.legacy) for s in samples] == [("can_run", Verdict.ALLOW)]
