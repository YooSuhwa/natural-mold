"""App-local archive/restore endpoints drive I22 enumeration, cascade and restore."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import scheduler
from app.authz.execution_lock_cursor import ExecutionProofError
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.agent_trigger import AgentTrigger
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import Organization
from app.models.token_usage import TokenUsage
from app.services import (
    conversation_run_queue_claim,
    conversation_run_queue_launch,
    conversation_run_queue_promotion,
    conversation_run_queue_recovery,
    conversation_run_queue_worker,
)
from app.services.orgs.versioning import etag
from app.services.tenants import (
    organization_archive_enumeration,
    organization_archive_terminalizer,
)
from tests.authz.organization_archive_workload import Workload, seed_workload
from tests.authz.tenant_organizations_fixture import Task7Case, seed_task7_case

pytest_plugins = [
    "tests.authz.tenant_organizations_fixture",
    "tests.authz.tenant_organization_lifecycle_app",
]
NAME = "Existing organization"
QUEUE_ENTRY_POINTS = (
    (conversation_run_queue_claim, "claim_next_input"),
    (conversation_run_queue_promotion, "promote_pending_input"),
    (conversation_run_queue_launch, "launch_claimed_input"),
    (conversation_run_queue_worker, "dispatch_next_for_conversation"),
    (conversation_run_queue_recovery, "recover_conversation_queue"),
)


class Spies:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: dict[str, list[Any]] = {}
        self._wrap(monkeypatch, organization_archive_terminalizer, "cascade_organization_archive")
        self._wrap(monkeypatch, organization_archive_enumeration, "lock_archive_scope")
        for name in ("add_trigger_job", "resume_trigger_job", "remove_trigger_job"):
            monkeypatch.setattr(scheduler, name, self._record(name))
        for module, name in QUEUE_ENTRY_POINTS:
            monkeypatch.setattr(module, name, self._record(name))

    def _record(self, name: str) -> Callable[..., None]:
        self.calls[name] = []
        return lambda *args, **_kwargs: self.calls[name].append(args)

    def _wrap(self, monkeypatch: pytest.MonkeyPatch, module: object, name: str) -> None:
        original = getattr(module, name)
        self.calls[name] = []

        async def spy(*args: Any, **kwargs: Any) -> Any:
            result = await original(*args, **kwargs)
            self.calls[name].append(result)
            return result

        monkeypatch.setattr(module, name, spy)


async def _org(db: AsyncSession, org_id: UUID) -> Organization:
    db.expire_all()
    row = await db.get(Organization, org_id)
    assert row is not None
    return row


async def _prepare(
    db: AsyncSession, client: AsyncClient, *, default: bool = False
) -> tuple[Task7Case, Workload]:
    case = await seed_task7_case(client, db)
    db.add(Organization(tenant_id=case.tenant_id, name="Sibling", slug=f"sib-{uuid4().hex}"))
    if default:
        (await _org(db, case.org_id)).is_default = True
    await db.commit()
    workload = await seed_workload(
        db, tenant_id=case.tenant_id, org_id=case.org_id, user_id=case.target_id
    )
    return case, workload


async def _post(client: AsyncClient, case: Task7Case, action: str, version: str) -> Response:
    return await client.post(
        f"/api/tenants/{case.tenant_id}/organizations/{case.org_id}/{action}",
        json={"confirm_name": NAME} if action == "archive" else None,
        headers={**case.auth.headers(), "If-Match": version},
    )


async def _snapshot(db: AsyncSession, w: Workload) -> tuple[object, ...]:
    db.expire_all()
    rows: list[object] = []
    for model, row_id in (
        (ConversationRunInput, w.pending_input_id),
        (ConversationRunInput, w.api_input_id),
        (ConversationRunInput, w.trigger_input_id),
        (ConversationRun, w.api_conversation_run_id),
        (ConversationRun, w.trigger_conversation_run_id),
        (ConversationRun, w.lease_run_id),
        (AgentApiRun, w.api_run_id),
        (AgentTriggerRun, w.trigger_run_id),
        (AgentTrigger, w.trigger_id),
        (AgentDeployment, w.deployment_id),
        (ConversationRunInput, w.done_input_id),
        (AgentApiRun, w.unlinked_api_run_id),
        (AgentTriggerRun, w.unlinked_trigger_run_id),
    ):
        row = await db.get(model, row_id)
        assert row is not None
        rows.append(
            (
                model.__name__,
                getattr(row, "status", None),
                getattr(row, "revision", None),
                getattr(row, "run_id", None),
                getattr(row, "is_active", None),
                getattr(row, "error_code", None),
            )
        )
    return tuple(rows)


async def _archive_audits(db: AsyncSession, org_id: UUID, action: str) -> int:
    statement = select(func.count()).select_from(AuditEvent)
    statement = statement.where(AuditEvent.target_id == str(org_id), AuditEvent.action == action)
    return int(await db.scalar(statement) or 0)


async def test_archive_endpoint_routes_through_enumeration_and_terminalization(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _prepare(db, lifecycle_client)
    spies = Spies(monkeypatch)
    before = await _org(db, case.org_id)
    before_version, before_epoch = etag(before.updated_at), before.authz_epoch

    response = await _post(lifecycle_client, case, "archive", before_version)

    assert response.status_code == 200 and response.json()["status"] == "archived"
    [scope] = spies.calls["lock_archive_scope"]
    assert scope.agent_ids == w.agent_ids
    # Exactly the four live conversations; the completed run's consumed input is excluded.
    assert scope.conversation_ids == w.conversation_ids
    assert w.done_conversation_id not in scope.conversation_ids
    assert w.done_input_id not in {item.id for item in scope.inputs}
    [outcome] = spies.calls["cascade_organization_archive"]
    assert outcome.paused_trigger_ids == (w.trigger_id,)
    assert spies.calls["remove_trigger_job"] == [(w.trigger_id,)]
    after = await _org(db, case.org_id)
    assert after.archived_at is not None and after.authz_epoch == before_epoch + 1
    assert response.headers["etag"] == etag(after.updated_at) != before_version
    assert await _archive_audits(db, case.org_id, "org.archive") == 1
    snapshot = await _snapshot(db, w)
    assert snapshot == (
        ("ConversationRunInput", "canceled", 2, None, None, None),
        ("ConversationRunInput", "failed", 1, w.api_conversation_run_id, None, None),
        ("ConversationRunInput", "failed", 1, w.trigger_conversation_run_id, None, None),
        ("ConversationRun", "failed", None, None, False, "organization_archived"),
        ("ConversationRun", "failed", None, None, False, "organization_archived"),
        ("ConversationRun", "running", None, None, True, None),
        ("AgentApiRun", "failed", None, None, None, "organization_archived"),
        ("AgentTriggerRun", "failed", None, None, None, None),
        ("AgentTrigger", "paused", None, None, None, None),
        ("AgentDeployment", "disabled", None, None, None, None),
        ("ConversationRunInput", "claimed", 1, w.done_run_id, None, None),
        ("AgentApiRun", "failed", None, None, None, "organization_archived"),
        ("AgentTriggerRun", "failed", None, None, None, None),
    )
    trigger_run = await db.get(AgentTriggerRun, w.trigger_run_id)
    assert trigger_run is not None and trigger_run.error_message == "organization_archived"
    assert int(await db.scalar(select(func.count()).select_from(TokenUsage)) or 0) == 0


async def test_restore_endpoint_is_metadata_only(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _prepare(db, lifecycle_client)
    archived = await _post(
        lifecycle_client, case, "archive", etag((await _org(db, case.org_id)).updated_at)
    )
    assert archived.status_code == 200
    archived_state = await _snapshot(db, w)
    spies = Spies(monkeypatch)

    restored = await _post(lifecycle_client, case, "restore", archived.headers["etag"])

    assert restored.status_code == 200 and restored.json()["status"] == "active"
    row = await _org(db, case.org_id)
    assert (row.status, row.archived_at, row.is_default) == ("active", None, False)
    assert await _snapshot(db, w) == archived_state
    assert spies.calls["cascade_organization_archive"] == []
    assert spies.calls["lock_archive_scope"] == []
    assert spies.calls["add_trigger_job"] == spies.calls["resume_trigger_job"] == []
    assert spies.calls["remove_trigger_job"] == []
    assert all(spies.calls[name] == [] for _module, name in QUEUE_ENTRY_POINTS)
    assert await _archive_audits(db, case.org_id, "org.restore") == 1


async def test_default_archive_never_enters_cascade(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _prepare(db, lifecycle_client, default=True)
    untouched = await _snapshot(db, w)
    spies = Spies(monkeypatch)

    response = await _post(
        lifecycle_client, case, "archive", etag((await _org(db, case.org_id)).updated_at)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "default_organization_archive_forbidden"
    assert spies.calls["cascade_organization_archive"] == []
    assert spies.calls["lock_archive_scope"] == []
    assert spies.calls["remove_trigger_job"] == []
    assert await _snapshot(db, w) == untouched
    assert (await _org(db, case.org_id)).status == "active"
    assert await _archive_audits(db, case.org_id, "org.archive") == 0


async def test_scope_drift_rolls_back_without_unscheduling(
    db: AsyncSession, lifecycle_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case, w = await _prepare(db, lifecycle_client)
    untouched = await _snapshot(db, w)
    spies = Spies(monkeypatch)

    async def drift(*_args: object, **_kwargs: object) -> None:
        raise ExecutionProofError("agent_tree_changed")

    monkeypatch.setattr(organization_archive_enumeration, "lock_archive_scope", drift)
    response = await _post(
        lifecycle_client, case, "archive", etag((await _org(db, case.org_id)).updated_at)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "agent_tree_changed"

    assert (await _org(db, case.org_id)).status == "active"
    assert await _snapshot(db, w) == untouched
    assert spies.calls["remove_trigger_job"] == []
    assert await _archive_audits(db, case.org_id, "org.archive") == 0
