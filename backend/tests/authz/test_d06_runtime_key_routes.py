"""D-06: every KEY route fails closed for a key owner without can_deploy (plan 9.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import prepared_execution, runtime_service
from app.authz import runtime
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun, AgentApiThread
from app.models.conversation import Conversation
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.authz_service_testkit import RecordingClient
from tests.conftest import TestSession
from tests.test_agent_runtime_public_api import _seed_deployed_agent_with_key

MSG = [{"role": "user", "content": "x"}]
DEPLOYMENT = "AGENT_DEPLOYMENT_NOT_FOUND"
RUN = {"agent_id": "{p}", "input": {"messages": MSG}}
# name -> (method, path, json body); {p} = deployment public id, {t} = an existing thread.
ROUTES = {
    "agents": ("GET", "/v1/agents", None),
    "threads": ("POST", "/v1/threads", {"agent_id": "{p}"}),
    "thread": ("GET", "/v1/threads/{t}", None),
    "wait": ("POST", "/v1/runs/wait", RUN),
    "stream": ("POST", "/v1/runs/stream", RUN),
    "thread_wait": ("POST", "/v1/threads/{t}/runs/wait", RUN),
    "thread_stream": ("POST", "/v1/threads/{t}/runs/stream", RUN),
    "dify_chat": ("POST", "/v1/agents/{p}/chat-messages", {"query": "x"}),
    "dify_workflow": ("POST", "/v1/workflows/run", {"inputs": {"agent_id": "{p}"}}),
    "openai": ("POST", "/v1/chat/completions", {"model": "{p}", "messages": MSG}),
}


def _fill(value: object, public_id: str, thread_id: str) -> object:
    if isinstance(value, str):
        return value.replace("{p}", public_id).replace("{t}", thread_id)
    if isinstance(value, dict):
        return {k: _fill(v, public_id, thread_id) for k, v in value.items()}
    return value


async def _counts(db: AsyncSession) -> tuple[int, ...]:
    models = (AgentApiRun, AgentApiThread, Conversation)
    return tuple(
        [await db.scalar(select(func.count()).select_from(model)) or 0 for model in models]
    )


def test_every_key_route_is_covered() -> None:
    assert len(ROUTES) == 10


@pytest.mark.anyio
@pytest.mark.parametrize("name", sorted(ROUTES))
async def test_key_route_denies_without_writing(
    name: str, client, db, test_app: FastAPI, monkeypatch
) -> None:
    async def never_invoke(*_a, **_k):
        raise AssertionError("a denied key must not reach the model")

    monkeypatch.setattr(runtime_service, "execute_agent_invoke", never_invoke)
    monkeypatch.setattr(runtime_service, "execute_agent_stream", never_invoke)
    _agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    auth = {"Authorization": f"Bearer {cleartext}"}
    seeded = await client.post("/v1/threads", headers=auth, json={"agent_id": deployment.public_id})
    thread_id = seeded.json()["id"]
    before = await _counts(db)
    method, path, body = ROUTES[name]
    service = AuthzService(RecordingClient(), mode="enforce")  # type: ignore[arg-type]
    test_app.dependency_overrides[get_authz] = lambda: service
    try:
        response = await client.request(
            method,
            str(_fill(path, deployment.public_id, thread_id)),
            headers=auth,
            json=_fill(body, deployment.public_id, thread_id),
        )
    finally:
        test_app.dependency_overrides.pop(get_authz, None)
    if name == "agents":  # the key stays valid; the deployment is filtered out
        assert response.status_code == 200 and response.json() == {"data": []}
    else:  # opaque: never 403 on /v1
        code = "AGENT_API_THREAD_NOT_FOUND" if name == "thread" else DEPLOYMENT
        assert response.status_code == 404 and response.json()["error"]["code"] == code
    async with TestSession() as check:
        assert await _counts(check) == before


@pytest.mark.asyncio
async def test_prepared_run_rechecks_before_locks_and_fails_closed(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    monkeypatch.setattr(runtime, "_current", AuthzService(RecordingClient(), mode="enforce"))  # type: ignore[arg-type]
    locked: list[object] = []

    async def locking(*args, **kwargs):
        locked.append(args)
        raise AssertionError("a denied owner must not reach the gateway locks")

    monkeypatch.setattr(prepared_execution.owned, "materialize_prepared_execution", locking)
    with pytest.raises(Exception) as denied:  # noqa: PT011 - coded AppError
        await runtime_service.execute_owned_wait(api.execution)
    assert getattr(denied.value, "code", None) == DEPLOYMENT
    assert kit.SENDS == [] and api.server.discoveries == 0
    state = await apikit.rows(api)
    assert state.run.runtime_materialized_at is None
    assert (state.run.status, state.run.error_code) == ("failed", DEPLOYMENT)


@pytest.mark.asyncio
async def test_prepared_run_decides_once_before_the_locked_resolver(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    ctx, agent = api.execution.context, f"agent:{api.execution.root_agent_id}"
    models = await db.scalars(select(Agent.model_id).where(Agent.user_id == ctx.user_id))
    me = f"user:{ctx.user_id}"
    # F-06: the run boundary also asks each model's catalog ``can_use`` (before the locks).
    used = {(me, "can_use", f"llm_model:{model}") for model in models}
    client = RecordingClient(allowed=frozenset({(me, "can_deploy", agent), *used}))
    monkeypatch.setattr(runtime, "_current", AuthzService(client, mode="enforce"))  # type: ignore[arg-type]
    at_lock: list[int] = []
    original = prepared_execution.owned.materialize_prepared_execution

    async def locking(*args, **kwargs):
        at_lock.append(len(client.queries))
        return await original(*args, **kwargs)

    monkeypatch.setattr(prepared_execution.owned, "materialize_prepared_execution", locking)
    assert "done" in await runtime_service.execute_owned_wait(api.execution)
    # Every decision (can_deploy, then F-06's plan) precedes the gateway locks.
    assert at_lock == [len(client.queries)] and at_lock[0] > 1
    deploy = [q for q in client.queries if getattr(q, "relation", None) == "can_deploy"]
    assert len(deploy) == 1  # the locked authorize_deployment reused the pre-lock decision
