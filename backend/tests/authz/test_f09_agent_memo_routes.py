"""F-09 routes: shared operating memo + proposals, and editors on memory settings (11.8, 9.7).

``GET memo`` and ``POST memo/proposals`` need ``agent#can_run``; ``PUT memo``, listing
and deciding proposals and the memory settings need ``agent#can_edit`` — a non-owner
editor now works on the agent's own rows (the D-01 owner-only deviation is closed).
"""

from collections.abc import Iterable, Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.agent_runtime import runtime_config
from app.authz import runtime
from app.authz.runtime import get_authz, legacy_service
from app.authz.service import AuthzService
from app.models.audit_event import AuditEvent
from app.services import agent_memo_service
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.e01_support import audit_event, seed
from tests.authz.f05_support import CALLER, shared_agent
from tests.conftest import TestSession

ME = f"user:{CALLER}"


@pytest.fixture(autouse=True)
def _data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path)


@pytest.fixture
def install(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    def _install(service: AuthzService) -> None:
        test_app.dependency_overrides[get_authz] = lambda: service
        monkeypatch.setattr(runtime, "_current", service)

    yield _install
    test_app.dependency_overrides.pop(get_authz, None)


def _enforce(agent_id: UUID, *perms: str, unavailable: bool = False) -> AuthzService:
    allowed: Iterable = {(ME, perm, f"agent:{agent_id}") for perm in perms}
    client = RecordingClient(allowed=frozenset(allowed), unavailable=unavailable)
    return AuthzService(client, mode="enforce")  # type: ignore[arg-type]


async def _agent() -> UUID:
    async with TestSession() as db:
        await seed(db)
        return (await shared_agent(db)).id  # owned by the other org member


def _code(response) -> str:
    return response.json()["error"]["code"]


async def test_runners_read_and_propose_editors_write_and_decide(
    client: AsyncClient, install
) -> None:
    agent_id = await _agent()
    url = f"/api/agents/{agent_id}/memo"
    agent_memo_service.memo_path(agent_id).parent.mkdir(parents=True)
    agent_memo_service.memo_path(agent_id).write_text("# Team\n")
    install(_enforce(agent_id, "can_run"))
    current = (await client.get(url)).json()
    assert current == {"agent_id": str(agent_id), "content": "# Team\n", "enabled": False}
    denied = await client.put(url, json={"enabled": True})
    assert denied.status_code == 403 and _code(denied) == "permission_required"
    listed = await client.get(f"{url}/proposals")
    assert listed.status_code == 403
    off = await client.post(f"{url}/proposals", json={"content": "Ship on Fridays"})
    assert off.status_code == 409 and _code(off) == "AGENT_MEMO_DISABLED"
    install(_enforce(agent_id, "can_run", "can_edit"))
    switched = await client.put(url, json={"enabled": True})
    assert switched.json() == {"agent_id": str(agent_id), "content": "# Team\n", "enabled": True}
    install(_enforce(agent_id, "can_run"))
    proposed = await client.post(f"{url}/proposals", json={"content": "Ship on Fridays"})
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["visible_to"] == "agent_editors"
    proposal_id = proposed.json()["id"]
    decision = f"{url}/proposals/{proposal_id}/decision"
    assert (await client.post(decision, json={"decision": "approve"})).status_code == 403
    assert agent_memo_service.read_memo(agent_id) == "# Team\n"

    install(_enforce(agent_id, "can_run", "can_edit"))
    assert [row["id"] for row in (await client.get(f"{url}/proposals")).json()] == [proposal_id]
    approved = await client.post(decision, json={"decision": "approve", "content": "Ship Fri"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert agent_memo_service.read_memo(agent_id) == "# Team\n- Ship Fri\n"
    again = await client.post(decision, json={"decision": "reject"})
    assert again.status_code == 404 and _code(again) == "MEMORY_PROPOSAL_NOT_FOUND"
    written = await client.put(url, json={"content": "# Rewritten\n"})
    assert written.status_code == 200 and agent_memo_service.read_memo(agent_id) == "# Rewritten\n"
    async with TestSession() as db:
        updates = (
            await db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.target_id == str(agent_id), AuditEvent.action == "agent_memo.update"
                )
                .order_by(AuditEvent.created_at)
            )
        ).all()
    assert [u.event_metadata for u in updates] == [
        {"length": None, "enabled": True},
        {"length": len("# Rewritten\n"), "enabled": None},
    ]
    assert {u.actor_user_id for u in updates} == {CALLER} != {updates[0].owner_user_id}
    decide = await audit_event(agent_id, "agent_memo.proposal_decide")
    assert decide.event_metadata == {"proposal_id": proposal_id, "decision": "approved"}


async def test_memo_routes_are_owner_only_in_legacy_and_fail_closed(
    client: AsyncClient, install
) -> None:
    agent_id = await _agent()
    url = f"/api/agents/{agent_id}/memo"
    install(legacy_service())
    assert (await client.get(url)).status_code == 404
    assert (await client.post(f"{url}/proposals", json={"content": "x"})).status_code == 404
    install(_enforce(agent_id))
    assert _code(await client.get(url)) == "AGENT_NOT_FOUND"
    install(_enforce(agent_id, unavailable=True))
    down = await client.get(url)
    assert down.status_code == 503 and _code(down) == "authz_unavailable"


async def test_a_non_owner_editor_manages_the_agents_memory_settings(
    client: AsyncClient, install
) -> None:
    agent_id = await _agent()
    url = f"/api/agents/{agent_id}/memory-settings"
    install(_enforce(agent_id, "can_run"))
    assert (await client.get(url)).status_code == 403
    install(_enforce(agent_id, "can_run", "can_edit"))
    patched = await client.patch(url, json={"memory_policy_override": "off"})
    assert patched.status_code == 200, patched.text
    assert (await client.get(url)).json()["memory_policy_override"] == "off"
    audited = await audit_event(agent_id, "agent.memory_settings_update")
    assert (audited.actor_user_id, audited.event_metadata) == (
        CALLER,
        {"changed_fields": ["memory_policy_override"]},
    )
    assert audited.owner_user_id != CALLER
    install(legacy_service())
    assert (await client.get(url)).status_code == 404  # legacy: still the owner's only
