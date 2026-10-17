"""H-04 operator sync screen API (plan 14.3, S27): gate, status, failures, actions."""

from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import create_csrf_token
from app.authz import audit_actions, ops_health
from app.authz.outbox import TupleOp, enqueue
from app.authz.reconcile_launch import get_reconcile_launcher
from app.authz.revocations import record_revocation
from app.authz.tuple_types import RelationTuple
from app.config import settings
from app.dependencies import CurrentUser, get_current_user
from app.exception_handlers import register_exception_handlers
from app.models.audit_event import AuditEvent
from app.models.authz import AuthzOutbox, AuthzRevocation, AuthzShadowDiff
from app.models.authz_reconcile_run import AuthzReconcileRun
from app.routers.admin_authz import get_ops_db, router
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, ensure_unit_scope

BASE = "/api/admin/authz"
NOW = datetime.now(UTC)


def _actor(super_user: bool = True) -> CurrentUser:
    return CurrentUser(id=uuid4(), email="op@example.com", name="Op", is_super_user=super_user)


@pytest.fixture(autouse=True)
def _legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "authz_mode", "legacy")
    ops_health.reset()


def _sync_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "authz_mode", "shadow")
    monkeypatch.setattr(settings, "openfga_store_id", "store")
    monkeypatch.setattr(settings, "openfga_model_id", "model")


class _Healthy:
    async def ping(self) -> None:
        return None


class _Opened:
    async def __aenter__(self) -> _Healthy:
        return _Healthy()

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
async def launched() -> list[UUID]:
    return []


async def _client(
    db: AsyncSession, actor: CurrentUser, launched: list[UUID]
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def session() -> AsyncGenerator[AsyncSession, None]:
        yield db

    async def current_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_ops_db] = session
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_reconcile_launcher] = lambda: launched.append
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = create_csrf_token(actor.id)
        client.cookies.set(settings.cookie_name_csrf, token)
        client.headers["X-CSRF-Token"] = token
        yield client


async def _seed(db: AsyncSession, actor: CurrentUser) -> UUID:
    """Two pending rows (one failing past the alert count), one stuck marker, diffs."""
    await ensure_unit_scope(db, actor)
    write = TupleOp("write", RelationTuple("user:a", "viewer", f"agent:{uuid4()}"))
    other = TupleOp("delete", RelationTuple("user:b", "viewer", f"skill:{uuid4()}"))
    [failing] = await enqueue(
        db, [write], source_table="organizations", source_id=TEST_ORG_ID, now=NOW
    )
    await enqueue(db, [other], source_table="authz_reconcile", source_id=uuid4(), now=NOW)
    secret = "AuthzUnavailable: OpenFGA write unavailable user:a agent:secret-name"
    await db.execute(
        update(AuthzOutbox)
        .where(AuthzOutbox.id == failing)
        .values(attempts=12, last_error=secret, next_attempt_at=NOW + timedelta(minutes=5))
    )
    marker = await record_revocation(
        db, org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, scope="grant", subject="user:a",
        object="agent:x",
    )  # fmt: skip
    marker.created_at = NOW - timedelta(minutes=30)
    db.add_all(
        [
            AuthzShadowDiff(route="/r", principal="user:a", object="agent:1", permission="can_run",
                            legacy=True, fga=False, count=3),
            AuthzShadowDiff(route="/r", principal="user:a", object="agent:2", permission="can_run",
                            legacy=False, fga=True, count=2),
            AuthzShadowDiff(route="/r", principal="user:a", object="skill:1", permission="can_use",
                            legacy=True, fga=False, count=1),
        ]
    )  # fmt: skip
    await db.commit()
    return failing


@pytest.mark.parametrize("mode", ["single_tenant", "multi_tenant"])
async def test_super_user_gate_in_both_deployment_modes(
    db: AsyncSession, launched: list[UUID], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(settings, "deployment_mode", mode)
    async for client in _client(db, _actor(super_user=False), launched):
        for path in ("/status", "/failures", f"/reconcile/{uuid4()}"):
            assert (await client.get(BASE + path)).status_code == 403
        assert (await client.post(f"{BASE}/reconcile")).status_code == 403
    async for client in _client(db, _actor(), launched):
        assert (await client.get(f"{BASE}/status")).status_code == 200


async def test_status_counts_backlog_markers_shadow_and_jobs_in_legacy(
    db: AsyncSession, launched: list[UUID]
) -> None:
    actor = _actor()
    await _seed(db, actor)
    async for client in _client(db, actor, launched):
        body = (await client.get(f"{BASE}/status")).json()
    assert (body["mode"], body["sync_enabled"], body["openfga"]["status"]) == (
        "legacy",
        False,
        "disabled",
    )
    assert body["outbox"]["pending"] == 2
    assert (body["outbox"]["retrying"], body["outbox"]["failed"]) == (1, 1)
    assert (body["revocations"]["pending"], body["revocations"]["stuck"]) == (1, 1)
    assert body["shadow"] == [
        {"type": "agent", "legacy_only": 3, "fga_only": 2},
        {"type": "skill", "legacy_only": 1, "fga_only": 0},
    ]
    assert body["reconcile"] == {"active": None, "last": None}
    assert body["delegations"] == {"broken_links": 0, "broken_last_24h": 0}
    assert {job["id"]: job["scheduled"] for job in body["jobs"]}["authz_outbox_drain"] is False


async def test_status_reports_openfga_health_outside_legacy(
    db: AsyncSession, launched: list[UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    _sync_on(monkeypatch)
    monkeypatch.setattr(ops_health, "open_client", lambda _config: _Opened())
    async for client in _client(db, _actor(), launched):
        body = (await client.get(f"{BASE}/status")).json()
    assert body["sync_enabled"] is True and body["openfga"]["status"] == "ok"
    assert body["openfga"]["last_success_at"] is not None
    scheduled = {job["id"] for job in body["jobs"] if job["scheduled"]}
    assert {"authz_outbox_drain", "authz_reconcile", "authz_backlog_alert"} <= scheduled


async def test_failures_show_company_and_resource_ids_only(
    db: AsyncSession, launched: list[UUID]
) -> None:
    actor = _actor()
    failing = await _seed(db, actor)
    async for client in _client(db, actor, launched):
        items = (await client.get(f"{BASE}/failures")).json()["items"]
    assert [item["id"] for item in items] == [str(failing)]
    [item] = items
    assert (item["tenant_id"], item["resource_type"], item["op"]) == (
        str(TEST_TENANT_ID),
        "agent",
        "write",
    )
    assert item["error_type"] == "AuthzUnavailable" and item["attempts"] == 12
    assert "secret-name" not in str(items) and "user:a" not in str(items)


async def test_retry_needs_csrf_and_sync_then_makes_the_row_due_and_audits(
    db: AsyncSession, launched: list[UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _actor()
    failing = await _seed(db, actor)
    path = f"{BASE}/failures/{failing}/retry"
    async for client in _client(db, actor, launched):
        assert (await client.post(path)).json()["error"]["code"] == "authz_sync_disabled"
        _sync_on(monkeypatch)
        forged = await client.post(path, headers={"X-CSRF-Token": "forged"})
        assert forged.status_code == 403
        response = await client.post(path)
        assert response.status_code == 200 and response.json()["id"] == str(failing)
        missing = uuid4()
        assert (await client.post(f"{BASE}/failures/{missing}/retry")).status_code == 404
    row = await db.get(AuthzOutbox, failing, populate_existing=True)
    assert row is not None
    assert row.next_attempt_at.replace(tzinfo=UTC) <= datetime.now(UTC)  # SQLite: naive
    assert row.attempts == 12 and row.processed_at is None
    events = list(await db.scalars(select(AuditEvent)))
    found = sorted((e.outcome, e.reason_code or "", e.target_id or "") for e in events)
    assert found == sorted(
        [
            ("denied", "authz_sync_disabled", str(failing)),
            ("success", "", str(failing)),
            ("denied", "not_found", str(missing)),
        ]
    )
    assert {(e.action, e.actor_user_id, e.tenant_id) for e in events} == {
        (audit_actions.AUTHZ_OUTBOX_RETRY, actor.id, None)
    }


async def test_reconcile_starts_one_audited_run_and_reports_progress(
    db: AsyncSession, launched: list[UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = _actor()
    await ensure_unit_scope(db, actor)
    async for client in _client(db, actor, launched):
        refused = await client.post(f"{BASE}/reconcile")
        assert (refused.status_code, refused.json()["error"]["code"]) == (
            409,
            "authz_sync_disabled",
        )
        _sync_on(monkeypatch)
        assert (
            await client.post(f"{BASE}/reconcile", headers={"X-CSRF-Token": "x"})
        ).status_code == 403
        started = await client.post(f"{BASE}/reconcile")
        assert started.status_code == 202
        job_id = started.json()["job_id"]
        again = await client.post(f"{BASE}/reconcile")
        assert (again.status_code, again.json()["error"]["code"]) == (
            409,
            "authz_reconcile_running",
        )
        run = (await client.get(f"{BASE}/reconcile/{job_id}")).json()
        assert (run["status"], run["trigger"], run["report"]) == ("queued", "manual", None)
        status = (await client.get(f"{BASE}/status")).json()["reconcile"]
        assert status["active"]["id"] == job_id
        assert (await client.get(f"{BASE}/reconcile/{uuid4()}")).status_code == 404
    assert launched == [UUID(job_id)]
    rows = list(await db.scalars(select(AuthzReconcileRun)))
    assert [(r.requested_by, r.active) for r in rows] == [(actor.id, True)]
    events = list(await db.scalars(select(AuditEvent)))
    assert {e.action for e in events} == {audit_actions.AUTHZ_RECONCILE_RUN}
    assert sorted((e.outcome, e.reason_code or "", e.target_id or "") for e in events) == [
        ("denied", "authz_reconcile_running", ""),
        ("denied", "authz_sync_disabled", ""),
        ("success", "", job_id),
    ]
    assert await db.scalar(select(AuthzRevocation.id)) is None
