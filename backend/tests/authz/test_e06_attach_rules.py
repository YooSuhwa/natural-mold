"""E-06: account resources attach only with ``can_share`` unless owned (plan D17, Q13, 10.6).

``require_attachable`` is the single gate every attach path calls (agent create/update,
builder confirm, blueprint create-agent — marketplace install creates a blueprint — and
the Assistant link tools). Today every path loads candidates owner-scoped, so the
foreign cases are exercised on the helper directly; the wiring is pinned per path.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.attach_rules import require_attachable
from app.authz.fake import FakeAuthz
from app.authz.service import AuthzService
from app.exceptions import AppError
from tests.authz.d04_support import OTHER_USER_ID, add_server, add_tool
from tests.authz.e01_support import ME, add_agent, add_credential, seed
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID

CTX = unit_context(TEST_USER_ID)
ACCOUNT_TYPES = frozenset({"tool", "mcp_server", "credential", "agent"})


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _runtime(monkeypatch: pytest.MonkeyPatch, *allowed: tuple[str, str]) -> None:
    tuples = frozenset((ME, permission, obj) for permission, obj in allowed)
    service = AuthzService(FakeAuthz(allowed=tuples), mode="enforce", enforce_types=ACCOUNT_TYPES)
    monkeypatch.setattr("app.authz.runtime._current", service)


async def _account_tool(db: AsyncSession, *, owner: uuid.UUID, system: bool = False):  # noqa: ANN202
    tool = await add_tool(db, owner=owner, name=f"t-{uuid.uuid4().hex[:6]}")
    tool.credential_id = (await add_credential(db, owner=owner)).id
    if system:
        tool.is_system, tool.user_id, tool.org_id, tool.tenant_id = True, None, None, None
    await db.commit()
    return tool


def _denied(error: AppError) -> tuple[int, str, object]:
    return error.status, error.code, (error.details or {}).get("required")


async def test_owned_and_platform_account_resources_need_no_decision(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    mine, system = (
        await _account_tool(db, owner=TEST_USER_ID),
        await _account_tool(db, owner=TEST_USER_ID, system=True),
    )
    plain_foreign = await add_tool(db, owner=OTHER_USER_ID, name="no-account")
    _runtime(monkeypatch)  # enforce with no tuples: any decision would refuse
    await require_attachable(db, CTX, tool_ids=[mine.id, system.id, plain_foreign.id])


async def test_a_foreign_account_tool_needs_can_share(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = await _account_tool(db, owner=OTHER_USER_ID)
    obj = f"tool:{tool.id}"
    _runtime(monkeypatch, ("can_use", obj))
    with pytest.raises(AppError) as used_only:
        await require_attachable(db, CTX, tool_ids=[tool.id])
    assert _denied(used_only.value) == (403, "permission_required", "can_share")
    _runtime(monkeypatch)
    with pytest.raises(AppError) as hidden:
        await require_attachable(db, CTX, tool_ids=[tool.id])
    assert hidden.value.status == 404
    _runtime(monkeypatch, ("can_use", obj), ("can_share", obj))
    await require_attachable(db, CTX, tool_ids=[tool.id])


async def test_foreign_mcp_servers_and_credentials_need_can_share(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, mcp_tool = await add_server(db, owner=OTHER_USER_ID)
    server.credential_id = (await add_credential(db, owner=OTHER_USER_ID)).id
    credential = await add_credential(db, owner=OTHER_USER_ID)
    await db.commit()
    srv, cred = f"mcp_server:{server.id}", f"credential:{credential.id}"
    _runtime(monkeypatch, ("can_use", srv), ("can_use", cred), ("can_share", cred))
    with pytest.raises(AppError) as error:
        await require_attachable(
            db, CTX, mcp_tool_ids=[mcp_tool.id], credential_ids=[credential.id]
        )
    assert _denied(error.value) == (403, "permission_required", "can_share")
    _runtime(monkeypatch, ("can_use", srv), ("can_share", srv), ("can_use", cred))
    with pytest.raises(AppError):
        await require_attachable(db, CTX, credential_ids=[credential.id])
    _runtime(
        monkeypatch, ("can_use", srv), ("can_share", srv), ("can_use", cred), ("can_share", cred)
    )
    await require_attachable(db, CTX, mcp_tool_ids=[mcp_tool.id], credential_ids=[credential.id])


async def test_legacy_refuses_a_foreign_account_resource(db: AsyncSession) -> None:
    tool = await _account_tool(db, owner=OTHER_USER_ID)
    with pytest.raises(AppError) as error:
        await require_attachable(db, CTX, tool_ids=[tool.id])
    assert (error.value.status, error.value.code) == (404, "not_found")


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    async def record(_db: object, _ctx: object, **ids: object) -> None:
        calls.append({key: sorted(map(str, value)) for key, value in ids.items()})  # type: ignore[call-overload]

    for module in ("agent_creation_service", "agent_update_service"):
        monkeypatch.setattr(f"app.services.{module}.require_attachable", record)
    return calls


async def test_agent_create_and_update_pass_the_added_resources(
    client: AsyncClient, db: AsyncSession, spy: list[dict]
) -> None:
    first, second = (
        await _account_tool(db, owner=TEST_USER_ID),
        await _account_tool(db, owner=TEST_USER_ID),
    )
    agent = await add_agent(db)
    body = {"name": "n", "system_prompt": "p", "model_id": str(agent.model_id)}
    created = await client.post("/api/agents", json={**body, "tool_ids": [str(first.id)]})
    assert created.status_code == 201, created.text
    assert spy[-1] == {"tool_ids": [str(first.id)], "mcp_tool_ids": []}
    path = f"/api/agents/{created.json()['id']}"
    updated = await client.put(path, json={"tool_ids": [str(first.id), str(second.id)]})
    assert updated.status_code == 200, updated.text
    assert spy[-1] == {"tool_ids": [str(second.id)], "mcp_tool_ids": []}  # only the added one
