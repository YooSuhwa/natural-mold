"""I24 H2/L1: archive stops in-flight legacy runs; a post-commit unschedule error is not a 500."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import scheduler
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_transitions
from app.services.agent_execution_runtime import run_owned_execution
from app.services.conversation_run_worker import (
    RunTaskRegistry,
    get_run_task_registry,
    reset_run_task_registry_for_tests,
)
from app.services.orgs.versioning import etag
from tests.authz.agent_execution_runtime_fixture import runtime_request, seed_runtime_run
from tests.authz.test_tenant_org_lifecycle_wiring import _org, _post, _prepare
from tests.conftest import TestSession

pytest_plugins = [
    "tests.authz.tenant_organizations_fixture",
    "tests.authz.tenant_organization_lifecycle_app",
]


@pytest.fixture(autouse=True)
def _registry() -> Iterator[RunTaskRegistry]:
    registry = RunTaskRegistry(worker_instance_id="i24-archive-worker")
    reset_run_task_registry_for_tests(registry)
    yield registry
    reset_run_task_registry_for_tests()


class Provider:
    """Counts fake provider calls made by a looping in-flight run."""

    def __init__(self) -> None:
        self.calls = 0

    async def loop(self) -> None:
        while True:
            self.calls += 1
            await asyncio.sleep(0.01)


def _own(registry: RunTaskRegistry, run_id: UUID, provider: Provider) -> asyncio.Task[None]:
    task = asyncio.ensure_future(provider.loop())
    registry.start(run_id, task)
    return task


async def _settle(task: asyncio.Task[None]) -> None:
    await asyncio.wait({task}, timeout=5)


async def test_archive_cancels_local_legacy_run_and_keeps_lease_owned_run(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _prepare(db, lifecycle_client)
    monkeypatch.setattr(scheduler, "remove_trigger_job", lambda *_a: None)
    registry = get_run_task_registry()
    legacy, lease = Provider(), Provider()
    legacy_task = _own(registry, w.trigger_conversation_run_id, legacy)
    lease_task = _own(registry, w.lease_run_id, lease)
    await asyncio.sleep(0.03)

    response = await _post(
        lifecycle_client, case, "archive", etag((await _org(db, case.org_id)).updated_at)
    )

    assert response.status_code == 200
    await _settle(legacy_task)
    assert legacy_task.cancelled()
    calls = legacy.calls
    await asyncio.sleep(0.05)
    assert legacy.calls == calls
    assert not lease_task.done()
    lease_task.cancel()


async def test_unschedule_failure_after_committed_archive_is_logged_not_500(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, _w = await _prepare(db, lifecycle_client)

    def broken(*_a: object) -> None:
        raise RuntimeError("scheduler down")

    monkeypatch.setattr(scheduler, "remove_trigger_job", broken)

    response = await _post(
        lifecycle_client, case, "archive", etag((await _org(db, case.org_id)).updated_at)
    )

    assert response.status_code == 200 and response.json()["status"] == "archived"
    assert (await _org(db, case.org_id)).status == "archived"


async def _fail_row(run_id: UUID) -> None:
    async with TestSession() as tx:
        row = await tx.get(ConversationRun, run_id)
        assert row is not None
        row.status, row.is_active, row.error_code = "failed", False, "organization_archived"
        await tx.commit()


async def test_heartbeat_cancels_local_task_when_row_became_inactive() -> None:
    run, _agent, _conversation = await seed_runtime_run()
    registry = get_run_task_registry()
    provider = Provider()
    task = _own(registry, run.id, provider)
    beat = asyncio.ensure_future(
        conversation_run_transitions._heartbeat_until_terminal(run.id, registry)
    )
    await _fail_row(run.id)

    await asyncio.wait_for(beat, 5)
    await _settle(task)

    assert task.cancelled()


async def test_owned_execution_poll_cancels_when_row_terminalized_elsewhere() -> None:
    run, agent, conversation = await seed_runtime_run()
    provider = Provider()
    started = asyncio.Event()

    async def operation() -> None:
        started.set()
        await provider.loop()

    owner = asyncio.ensure_future(
        run_owned_execution(runtime_request(run, agent, conversation), operation)
    )
    await asyncio.wait_for(started.wait(), 5)
    await _fail_row(run.id)

    await _settle(owner)

    assert owner.cancelled()
    calls = provider.calls
    await asyncio.sleep(0.05)
    assert provider.calls == calls
    async with TestSession() as check:
        stored = await check.get(ConversationRun, run.id)
        assert stored is not None and stored.error_code == "organization_archived"
