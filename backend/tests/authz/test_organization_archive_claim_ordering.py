"""Archive versus queue claim and trigger claim, both winner orders (internal I22).

aiosqlite cannot hold competing row locks, so each race runs its two winner
orders sequentially and asserts the exact persisted outcome.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.trigger_execution_claim import claim_trigger_run
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.conversation_run_queue_claim import claim_next_input
from tests.authz.organization_archive_workload import Workload
from tests.authz.tenant_organizations_fixture import Task7Case
from tests.authz.test_organization_archive_concurrency import _case, _context, _lifecycle

pytest_plugins = [
    "tests.authz.tenant_organizations_fixture",
    "tests.authz.tenant_organization_lifecycle_app",
]


async def _input(db: AsyncSession, w: Workload) -> ConversationRunInput:
    db.expire_all()
    row = await db.get(ConversationRunInput, w.pending_input_id)
    assert row is not None
    return row


async def test_queue_claim_first_then_archive_fails_unmaterialized_run(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case, w = await _case(db, lifecycle_client)
    claimed = await claim_next_input(
        db, conversation_id=w.pending_conversation_id, context=_context(case)
    )
    assert claimed is not None and claimed.run.runtime_materialized_at is None
    run_id = claimed.run.id
    await db.commit()

    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200

    row = await _input(db, w)
    assert (row.status, row.run_id) == ("failed", run_id)
    run = await db.get(ConversationRun, run_id)
    assert run is not None
    assert (run.status, run.is_active, run.error_code) == ("failed", False, "organization_archived")


async def test_archive_first_then_queue_claim_finds_nothing(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case, w = await _case(db, lifecycle_client)
    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200
    runs_before = int(await db.scalar(select(func.count()).select_from(ConversationRun)) or 0)

    with pytest.raises(HTTPException) as denied:
        await claim_next_input(
            db, conversation_id=w.pending_conversation_id, context=_context(case)
        )
    await db.rollback()

    assert denied.value.status_code == 404
    row = await _input(db, w)
    assert (row.status, row.run_id, row.revision) == ("canceled", None, 2)
    assert int(await db.scalar(select(func.count()).select_from(ConversationRun)) or 0) == (
        runs_before
    )


async def _fresh_trigger(db: AsyncSession, case: Task7Case, w: Workload) -> uuid.UUID:
    trigger = AgentTrigger(
        agent_id=w.idle_agent_id,
        user_id=case.target_id,
        org_id=case.org_id,
        tenant_id=case.tenant_id,
        trigger_type="interval",
        schedule_config={"minutes": 5},
        input_message="tick",
        next_run_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(trigger)
    await db.commit()
    return trigger.id


async def _trigger_runs(db: AsyncSession, trigger_id: uuid.UUID) -> list[AgentTriggerRun]:
    db.expire_all()
    rows = await db.scalars(select(AgentTriggerRun).where(AgentTriggerRun.trigger_id == trigger_id))
    return list(rows)


async def test_trigger_claim_first_then_archive_fails_unmaterialized_trigger_run(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case, w = await _case(db, lifecycle_client)
    trigger_id = await _fresh_trigger(db, case, w)
    claim = await claim_trigger_run(db, str(trigger_id), trigger_id, force=False)
    assert claim is not None
    await db.commit()

    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200

    [run] = await _trigger_runs(db, trigger_id)
    assert (run.status, run.error_message) == ("failed", "organization_archived")
    assert run.finished_at is not None
    trigger = await db.get(AgentTrigger, trigger_id)
    assert trigger is not None and trigger.status == "paused"


async def test_archive_first_pauses_trigger_and_claim_creates_no_run(
    db: AsyncSession, lifecycle_client: AsyncClient
) -> None:
    case, w = await _case(db, lifecycle_client)
    trigger_id = await _fresh_trigger(db, case, w)
    assert (await _lifecycle(db, lifecycle_client, case, "archive")).status_code == 200

    assert await claim_trigger_run(db, str(trigger_id), trigger_id, force=False) is None
    await db.rollback()

    assert await _trigger_runs(db, trigger_id) == []
    trigger = await db.get(AgentTrigger, trigger_id)
    assert trigger is not None and (trigger.status, trigger.next_run_at) == ("paused", None)
