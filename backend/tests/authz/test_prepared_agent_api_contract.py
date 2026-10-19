"""I17: flag-off / unsupported-shape fallback and the unchanged HTTP response contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import runtime_service
from app.models.agent import Agent
from app.services import agent_execution_runtime
from tests.authz import prepared_agent_api_testkit as apikit
from tests.authz import prepared_materialization_testkit as kit


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_unsupported_shape_or_flag_off_uses_the_legacy_path(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    apikit.enable(monkeypatch, enabled)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch)
    agent = await db.get(Agent, api.seeded.graph.agent_id)
    assert agent is not None
    agent.middleware_configs = [{"type": "model_retry", "params": {}}]
    await db.commit()
    calls: list[Any] = []
    materialized: list[Any] = []
    original = agent_execution_runtime.materialize_prepared_execution

    async def legacy(cfg: Any, messages: Any, **kwargs: Any) -> str:
        calls.append((cfg, kwargs["moldy_source"]))
        return "legacy"

    async def materialize_spy(*args: Any, **kwargs: Any) -> Any:
        materialized.append(1)
        return await original(*args, **kwargs)

    monkeypatch.setattr(runtime_service, "execute_agent_invoke", legacy)
    monkeypatch.setattr(agent_execution_runtime, "materialize_prepared_execution", materialize_spy)

    assert await runtime_service.execute_owned_wait(api.execution) == "legacy"

    assert calls == [(api.execution.config, "api")]
    assert materialized == ([1] if enabled else []) and kit.SENDS == []
    state = await apikit.rows(api)
    assert state.run.status == "completed" and state.run.runtime_materialized_at is None


async def _post_wait(client: AsyncClient, api: apikit.ApiRun, agent_id: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/runs/wait",
        headers={"Authorization": f"Bearer {api.cleartext}"},
        json={"agent_id": agent_id, "input": {"messages": apikit.MESSAGES}},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_http_wait_response_contract_is_unchanged(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_client: AsyncClient
) -> None:
    # The router's legacy pre-acceptance config path cannot load a stored child here.
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, with_child=False)
    agent = await db.get(Agent, api.execution.root_agent_id)
    assert agent is not None
    agent.runtime_policy = {"version": 1}  # fresh API conversations snapshot a stored policy
    await db.commit()
    agent_id = agent.runtime_name or f"agent_{agent.id.hex[:12]}"

    async def legacy(*_a: Any, **_k: Any) -> str:
        return "done"

    monkeypatch.setattr(runtime_service, "execute_agent_invoke", legacy)
    legacy_body = await _post_wait(raw_client, api, agent_id)
    apikit.enable(monkeypatch)
    apikit.forbid_legacy(monkeypatch)
    prepared_body = await _post_wait(raw_client, api, agent_id)

    assert [api.seeded.root_model] == kit.SENDS
    assert set(prepared_body) == set(legacy_body)
    for body in (legacy_body, prepared_body):
        assert body["status"] == "succeeded" and body["output"] == {"answer": "done"}
    assert prepared_body["id"] != legacy_body["id"]
