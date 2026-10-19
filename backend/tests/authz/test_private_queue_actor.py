"""Queued work rechecks the persisted conversation's organization before runtime assembly."""

from typing import Literal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import OrganizationMember, Tenant
from app.services import conversation_run_queue_service, conversation_run_queue_worker
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID
from tests.integration._seed import seed_conversation_with_agent


@pytest.mark.parametrize("change", ["membership", "company"])
async def test_claimed_queue_denies_revoked_conversation_authority(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, change: Literal["membership", "company"]
) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=unit_context(TEST_USER_ID),
        client_request_id="revoked-org-fixture",
        source="chat",
        input_payload={"messages": [{"role": "user", "content": "must not execute"}]},
        attachment_ids=[],
        checkpoint_id=None,
        priority=0,
    )
    await db.commit()
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=unit_context(TEST_USER_ID)
    )
    assert claimed is not None
    if change == "membership":
        await db.execute(
            delete(OrganizationMember).where(
                OrganizationMember.org_id == TEST_ORG_ID,
                OrganizationMember.user_id == TEST_USER_ID,
            )
        )
    else:
        await db.execute(
            update(Tenant).where(Tenant.id == TEST_TENANT_ID).values(status="suspended")
        )
    await db.commit()
    assemble = AsyncMock(side_effect=AssertionError("Runtime must not be assembled"))
    monkeypatch.setattr(conversation_run_queue_worker, "resolve_agent_context", assemble)
    await conversation_run_queue_worker.launch_claimed_input(queued.id)
    assemble.assert_not_awaited()
    await db.refresh(claimed.run)
    failed = await db.get(ConversationRunInput, queued.id, populate_existing=True)
    assert failed is not None and failed.status == "failed"
    assert claimed.run.status == "failed"
    assert claimed.run.error_code == "queue_rehydrate_failed"
