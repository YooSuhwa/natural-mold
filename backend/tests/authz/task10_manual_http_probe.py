"""App-local ASGI probe: archive/restore + Agent API requests with the lease flag on (I23).

Ordinary (aiosqlite) and deliberately not ``test_*``-named: run it explicitly,
``pytest -q -m "not integration" tests/authz/task10_manual_http_probe.py`` or
``PYTHONPATH=. .venv/bin/python tests/authz/task10_manual_http_probe.py``. It
mounts I21's app-local lifecycle router, drives one accepted ``/v1/runs/wait``
through the prepared lease (scripted, permit-guarded provider + fake MCP), then
archives, retries the API request, restores and retries again, asserting exact
HTTP status/body plus provider/MCP/persistence counters at every step.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentDeployment
from app.models.audit_event import AuditEvent
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.task10_company_admin import lifecycle, seed_company_admin
from tests.conftest import TestSession

pytest_plugins = ["tests.authz.tenant_organization_lifecycle_app"]


async def _counts(db: AsyncSession) -> tuple[int, int, int]:
    db.expire_all()
    return (
        int(await db.scalar(select(func.count()).select_from(ConversationRun)) or 0),
        int(await db.scalar(select(func.count()).select_from(AgentApiRun)) or 0),
        int(
            await db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action.in_(("org.archive", "org.restore")))
            )
            or 0
        ),
    )


async def _wait(client: AsyncClient, key: str, agent: str) -> Response:
    return await client.post(
        "/v1/runs/wait",
        headers={"Authorization": f"Bearer {key}"},
        json={"agent_id": agent, "input": {"messages": [{"role": "user", "content": "hi"}]}},
    )


async def test_manual_http_archive_restore_and_api_request(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_client: AsyncClient,
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, with_child=False)
    apikit.forbid_legacy(monkeypatch)
    context = api.seeded.graph.context
    agent = await db.get(Agent, api.seeded.graph.agent_id)
    assert agent is not None
    agent.runtime_policy = {"version": 1}
    await db.commit()
    deployment = await db.scalar(
        select(AgentDeployment).where(AgentDeployment.agent_id == agent.id)
    )
    assert deployment is not None
    public_id, deployment_id = deployment.public_id, deployment.id
    admin = await seed_company_admin(db, context)
    base = await _counts(db)

    accepted = await _wait(lifecycle_client, api.cleartext, public_id)
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert (body["status"], body["output"], body["agent_id"]) == (
        "succeeded",
        {"answer": "done"},
        public_id,
    )
    assert [api.seeded.root_model] == kit.SENDS and api.server.discoveries == 1
    assert await _counts(db) == (base[0] + 1, base[1] + 1, base[2])
    accepted_run = await db.scalar(
        select(ConversationRun)
        .where(ConversationRun.conversation_id != api.execution.conversation_id)
        .where(ConversationRun.status == "completed")
        .order_by(ConversationRun.created_at.desc())
    )
    assert accepted_run is not None and accepted_run.runtime_materialized_at is not None
    assert accepted_run.runtime_materialization_sha256

    archived = await lifecycle(lifecycle_client, TestSession, admin, "archive")
    assert archived.status_code == 200, archived.text
    assert (archived.json()["id"], archived.json()["status"]) == (str(admin.org_id), "archived")
    after_archive = await _counts(db)
    assert after_archive == (base[0] + 1, base[1] + 1, base[2] + 1)
    db.expire_all()
    disabled = await db.get(AgentDeployment, deployment_id)
    assert disabled is not None and disabled.status == "disabled"

    denied = await _wait(lifecycle_client, api.cleartext, public_id)
    # The key's organization is archived: authentication refuses before any run row.
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "AGENT_API_KEY_INVALID"
    assert [api.seeded.root_model] == kit.SENDS and api.server.discoveries == 1
    assert await _counts(db) == after_archive

    restored = await lifecycle(
        lifecycle_client, TestSession, admin, "restore", version=archived.headers["etag"]
    )
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    db.expire_all()
    org = await db.get(Organization, admin.org_id)
    still_disabled = await db.get(AgentDeployment, deployment_id)
    assert org is not None and (org.status, org.archived_at) == ("active", None)
    assert still_disabled is not None and still_disabled.status == "disabled"
    # Restore never re-enables the deployment: the retried request is still refused.
    retried = await _wait(lifecycle_client, api.cleartext, public_id)
    assert retried.status_code == 404
    assert retried.json()["error"]["code"] == "AGENT_DEPLOYMENT_NOT_FOUND"
    assert [api.seeded.root_model] == kit.SENDS and api.server.discoveries == 1
    assert await _counts(db) == (after_archive[0], after_archive[1], after_archive[2] + 1)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider", "-m", "not integration"]))
