"""F-13: the Builder Assistant run refuses once the actor's ``agent#can_edit`` is revoked
(plan 11.0 "빌더 어시스턴트", 11.7)."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent_runtime.assistant import assistant_agent
from app.exceptions import AppError
from tests.authz.agent_route_fixture import ME, OTHER_USER_ID, enforce, seed_agent
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

VIEW = ("can_view", "can_view_config", "can_run")


@pytest.fixture
def seams(monkeypatch: pytest.MonkeyPatch) -> Mock:
    model = SimpleNamespace(provider="openai", model_name="m", api_key=None, base_url=None)
    monkeypatch.setattr(assistant_agent, "resolve_system_model", AsyncMock(return_value=model))
    monkeypatch.setattr(assistant_agent, "create_chat_model", Mock())
    monkeypatch.setattr(assistant_agent, "get_checkpointer", Mock())
    build = Mock(return_value="graph")
    monkeypatch.setattr(assistant_agent, "build_agent", build)
    return build


async def _build(agent_id: str) -> object:
    async with TestSession() as db:
        return await assistant_agent.build_assistant_agent(
            db, uuid.UUID(agent_id), unit_context(TEST_USER_ID), "thread"
        )


async def test_assistant_run_refuses_after_can_edit_is_revoked(
    monkeypatch: pytest.MonkeyPatch, seams: Mock
) -> None:
    shared = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    agent = uuid.UUID(shared)
    granted = [(ME, permission, agent) for permission in (*VIEW, "can_edit")]
    monkeypatch.setattr("app.authz.runtime._current", enforce(*granted))
    assert await _build(shared) == "graph"
    seams.assert_called_once()

    revoked = [(ME, permission, agent) for permission in VIEW]
    monkeypatch.setattr("app.authz.runtime._current", enforce(*revoked))
    with pytest.raises(AppError) as denied:
        await _build(shared)
    assert (denied.value.status, denied.value.code) == (404, "AGENT_NOT_FOUND")
    seams.assert_called_once()  # no second graph is built
