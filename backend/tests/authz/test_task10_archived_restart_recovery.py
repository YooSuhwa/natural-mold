"""Restart recovery of a lost lease must not depend on the organization being active (I23).

Parent Task 10: a worker restart/crash has no lease and must terminalize the
paired run rather than reconstruct it. Without archive, recovery fails the
run ``prepared_run_lease_missing``; after an organization archive (which
deliberately leaves the paired, lease-owned run alone) both recovery paths
currently skip it, so it stays ``running``/active forever. The archived case
is a regression documenting that gap until the recovery service is fixed.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import LEASE_MISSING
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.services import conversation_run_queue_worker, conversation_run_worker
from app.services.conversation_run_prepared_recovery import terminalize_orphaned_prepared_runs
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_queue_testkit as qkit
from tests.authz.task10_company_admin import lifecycle, seed_company_admin
from tests.conftest import TestSession

pytest_plugins = ["tests.authz.tenant_organization_lifecycle_app"]


async def _recover(mode: str) -> Any:
    if mode == "queue_recovery":
        return await conversation_run_queue_worker.recover_conversation_queue()
    return await terminalize_orphaned_prepared_runs(
        session_factory=TestSession, stale_before=utc_now_naive() - timedelta(hours=1)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["queue_recovery", "orphan_sweep"])
@pytest.mark.parametrize("archived", [False, True])
async def test_lost_lease_is_terminalized_without_reconstruction(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_client: AsyncClient,
    mode: str,
    archived: bool,
) -> None:
    harness, input_id, run_id, lost, registry = await qkit.paired_then_lost(
        db, tmp_path, monkeypatch
    )
    if archived:
        admin = await seed_company_admin(db, harness.seeded.graph.context)
        response = await lifecycle(lifecycle_client, TestSession, admin, "archive")
        assert response.status_code == 200
    worker = conversation_run_worker.get_run_task_registry().worker_instance_id
    stale = utc_now_naive() - timedelta(days=1)
    await db.execute(
        update(ConversationRun)
        .where(ConversationRun.id == run_id)
        .values(worker_instance_id=worker, heartbeat_at=stale)
    )
    await db.commit()
    resolutions = len(harness.resolutions)

    try:
        await _recover(mode)
        db.expire_all()
        row = await db.get(ConversationRun, run_id)
        assert row is not None
        assert (row.status, row.is_active, row.error_code) == ("failed", False, LEASE_MISSING)
        assert len(harness.resolutions) == resolutions and harness.prepare_agent_calls == 0
        assert kit.SENDS == [] and registry.state(run_id) is None
    finally:
        await lost.terminalize(run_id)
