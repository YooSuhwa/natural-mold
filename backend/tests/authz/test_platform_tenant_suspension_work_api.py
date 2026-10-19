"""Suspension is reversible: accepted and queued work is neither canceled nor executed."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_run_registry import reset_prepared_run_registry_for_tests
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.chat.chat_execution_preparation import create_conversation
from app.services.conversation_run_queue_claim import claim_next_input
from app.services.conversation_run_queue_worker import dispatch_next_for_conversation
from tests.authz.platform_tenant_suspension_workload import (
    activate_lease,
    provider_permit,
    seed_suspension_workload,
)
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.platform_tenant_lifecycle_app import LifecycleCase

pytest_plugins = ["tests.authz.platform_tenant_lifecycle_app"]


@pytest.fixture(autouse=True)
def _fresh_registry() -> Iterator[None]:
    yield
    reset_prepared_run_registry_for_tests()


async def _run_state(run_id: object) -> tuple[str, bool, str | None]:
    async with TestSession() as db:
        run = await db.get(ConversationRun, run_id)
        assert run is not None
        return run.status, run.is_active, run.error_code


async def test_running_lease_is_denied_its_next_permit_but_never_canceled(
    db: AsyncSession, lifecycle_case: LifecycleCase
) -> None:
    case = lifecycle_case
    work = await seed_suspension_workload(db, case)
    lease = await activate_lease(
        case.context, work.agent_id, work.conversation_id, work.running_run_id
    )
    async with provider_permit(lease, TestSession) as permit:
        assert permit.archived_bypass is False
    before = await _run_state(work.running_run_id)
    assert before == ("running", True, None)

    assert (await case.transition("suspend")).status_code == 200
    with pytest.raises(DispatchDeniedError) as denied:
        async with provider_permit(lease, TestSession):
            pytest.fail("a suspended company must not receive a dispatch permit")
    assert denied.value.code == "tenant_suspended"
    assert await _run_state(work.running_run_id) == before

    assert (await case.transition("resume")).status_code == 200
    async with provider_permit(lease, TestSession):
        pass
    assert await _run_state(work.running_run_id) == before


async def test_pending_input_survives_suspension_and_is_claimable_after_resume(
    db: AsyncSession, lifecycle_case: LifecycleCase
) -> None:
    case = lifecycle_case
    work = await seed_suspension_workload(db, case)
    conversation = await create_conversation(db, work.agent_id, "Queued", context=case.context)
    pending = ConversationRunInput(
        conversation_id=conversation.id,
        agent_id=work.agent_id,
        user_id=case.member_id,
        client_request_id="task13-queued",
        source="chat",
        status="pending",
        position=1,
        revision=1,
        input_payload={"message": "queued before suspension"},
    )
    db.add(pending)
    await db.commit()
    runs_for = select(func.count()).select_from(ConversationRun)
    runs_for = runs_for.where(ConversationRun.conversation_id == conversation.id)

    assert (await case.transition("suspend")).status_code == 200
    assert await dispatch_next_for_conversation(conversation.id) is None
    async with TestSession() as claim_db:
        with pytest.raises(HTTPException) as refused:
            await claim_next_input(claim_db, conversation_id=conversation.id, context=case.context)
    # The queue keeps its opaque 404; the cause is the fresh suspension check.
    codes: list[str] = []
    cause = refused.value.__context__
    while cause is not None:
        if isinstance(cause, AppError):
            codes.append(cause.code)
        cause = cause.__context__
    assert "tenant_suspended" in codes
    async with TestSession() as check:
        stored = await check.get(ConversationRunInput, pending.id)
        assert stored is not None
        assert (stored.status, stored.run_id, stored.revision) == ("pending", None, 1)
        assert await check.scalar(runs_for) == 0

    assert (await case.transition("resume")).status_code == 200
    async with TestSession() as claim_db:
        claimed = await claim_next_input(
            claim_db, conversation_id=conversation.id, context=case.context
        )
        assert claimed is not None and claimed.input.id == pending.id
        await claim_db.rollback()
