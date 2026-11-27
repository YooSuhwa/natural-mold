"""An orphan scan must not terminalize a run whose owner or heartbeat changed."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import LEASE_MISSING
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import Tenant
from app.services import conversation_run_prepared_recovery as recovery
from app.services.conversation_run_worker import get_run_task_registry
from tests.authz import prepared_queue_testkit as qkit
from tests.conftest import TestSession


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant_suspended", [False, True])
@pytest.mark.parametrize("change", ["fresh_heartbeat", "new_owner", "unchanged", "local_owner"])
async def test_orphan_scan_rechecks_locked_owner_and_heartbeat(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    tenant_suspended: bool,
) -> None:
    harness, input_id, run_id, lost, _ = await qkit.paired_then_lost(db, tmp_path, monkeypatch)
    fresh = utc_now_naive()
    cutoff = fresh - timedelta(hours=1)
    stale = cutoff - timedelta(seconds=1)
    owner = get_run_task_registry().worker_instance_id if change == "local_owner" else "remote"
    try:
        if tenant_suspended:
            await db.execute(
                update(Tenant)
                .where(Tenant.id == harness.seeded.graph.context.tenant_id)
                .values(status="suspended")
            )
        await db.execute(
            update(ConversationRun)
            .where(ConversationRun.id == run_id)
            .values(
                worker_instance_id=owner, heartbeat_at=fresh if change == "local_owner" else stale
            )
        )
        await db.commit()

        terminalize = recovery._terminalize  # noqa: SLF001
        candidates_attempted = 0

        async def after_scan(*args: Any, **kwargs: Any) -> bool:
            nonlocal candidates_attempted
            candidates_attempted += 1
            if change in ("fresh_heartbeat", "new_owner"):
                # The candidate query has closed. Commit the owner's independent
                # transaction before recovery acquires its terminalization lock.
                async with TestSession() as writer:
                    values = (
                        {"heartbeat_at": fresh}
                        if change == "fresh_heartbeat"
                        else {"worker_instance_id": "other-remote"}
                    )
                    await writer.execute(
                        update(ConversationRun).where(ConversationRun.id == run_id).values(**values)
                    )
                    await writer.commit()
            return await terminalize(*args, **kwargs)

        with monkeypatch.context() as timing:
            timing.setattr(recovery, "_terminalize", after_scan)
            failed = await recovery.terminalize_orphaned_prepared_runs(
                session_factory=TestSession, stale_before=cutoff
            )
        assert candidates_attempted == 1
        db.expire_all()
        run = await db.get(ConversationRun, run_id)
        linked_input = await db.get(ConversationRunInput, input_id)
        assert run is not None and linked_input is not None
        if change in ("fresh_heartbeat", "new_owner"):
            assert failed == []
            assert (run.status, run.is_active, run.error_code) == ("running", True, None)
            assert linked_input.status == "claimed"
        else:
            assert failed == [run_id]
            assert (run.status, run.is_active, run.error_code) == ("failed", False, LEASE_MISSING)
            assert linked_input.status == "failed"
    finally:
        await lost.terminalize(run_id)
