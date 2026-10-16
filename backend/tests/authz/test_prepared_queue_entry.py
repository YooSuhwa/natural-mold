"""Direct queue claim and worker recovery through the prepared lease (internal I19).

The queued input is claimed gateway-first and committed, then launched; the
worker materializes the lease (locks -> marker pair + ``queued -> running`` ->
commit -> activate) before the first permit-guarded provider send and reads no
Agent/User/config afterwards. After a restart a claimed run without the pair
is freshly prepared (paired-run recovery: ``test_prepared_queue_recovery``).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.agent_runtime.prepared_run_registry import (
    get_prepared_run_registry,
)
from app.services import (
    conversation_run_queue_worker,
)
from tests.authz import prepared_materialization_testkit as kit
from tests.authz import prepared_queue_testkit as qkit
from tests.authz.test_organization_archive_concurrency import LIFECYCLE, lock_trace


@pytest.mark.asyncio
async def test_direct_queued_run_is_prepared_and_launched(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, input_id = await qkit.queue_harness(db, tmp_path, monkeypatch)
    registry = get_prepared_run_registry()
    activate = registry.activate

    async with lock_trace(monkeypatch) as trace:

        def after_commit(_session: Session) -> None:
            trace.append("COMMIT")

        def spy(run_id: uuid.UUID, *, token: str, digest: str) -> None:
            trace.append(f"ACTIVATE:{len(kit.SENDS)}")
            activate(run_id, token=token, digest=digest)

        monkeypatch.setattr(registry, "activate", spy)
        event.listen(Session, "after_commit", after_commit)
        try:
            claimed = await conversation_run_queue_worker.dispatch_next_for_conversation(
                harness.seeded.request.conversation_id
            )
            assert claimed is not None and claimed.id == input_id and claimed.run_id
            row = await qkit.await_run(claimed.run_id)
        finally:
            event.remove(Session, "after_commit", after_commit)

    assert row.status == "completed", (row.status, row.error_code, row.error_message)
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    assert harness.resolutions == [(False, True)] and harness.prepare_agent_calls == 0
    assert [harness.seeded.root_model] == kit.SENDS
    # Claim: gateway (agent lifecycle) before Conversation and Input locks.
    assert trace.index(LIFECYCLE) < trace.index("conversations")
    assert trace.index("conversations") < trace.index("conversation_run_inputs")
    # Launch: re-entered locks -> marker + running -> COMMIT -> activate, no send yet.
    activation = trace.index("ACTIVATE:0")
    assert trace[activation - 1] == "COMMIT"
    assert "conversation_runs" in trace[trace.index("COMMIT") : activation]


@pytest.mark.asyncio
async def test_restart_freshly_prepares_claimed_run_without_pair(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness, _input_id = await qkit.queue_harness(db, tmp_path, monkeypatch)
    run_id = await qkit.claim(db, harness)
    qkit.restart(monkeypatch)

    assert await conversation_run_queue_worker.recover_conversation_queue() == 1
    row = await qkit.await_run(run_id)

    assert row.status == "completed", (row.status, row.error_code, row.error_message)
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    assert harness.resolutions == [(False, True)]
    assert [harness.seeded.root_model] == kit.SENDS
