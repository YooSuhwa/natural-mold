"""``RuntimeOwnershipConflict`` escapes the shared owned-run lifecycle unwrapped.

anyio 4 task groups wrap exceptions raised inside their body in an
``ExceptionGroup``; the lifecycle stores the outcome and raises after the task
group, so callers' ``except RuntimeOwnershipConflict`` keeps working.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agent_api import prepared_execution
from app.agent_runtime import agent_stream_runner
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.agent_execution_runtime import RuntimeOwnershipConflict, run_owned_execution
from app.services.conversation_run_worker import get_run_task_registry
from tests.authz.agent_execution_runtime_fixture import runtime_request, seed_runtime_run
from tests.conftest import TestSession


async def _steal_ownership(run_id: object) -> None:
    async with TestSession() as db:
        row = await db.get(ConversationRun, run_id)
        assert row is not None
        row.worker_instance_id = "another-worker"
        await db.commit()


@pytest.mark.asyncio
async def test_legacy_owner_changed_is_catchable_directly() -> None:
    run, agent, conversation = await seed_runtime_run()

    async def operation() -> str:
        await _steal_ownership(run.id)
        return "done"

    with pytest.raises(RuntimeOwnershipConflict) as caught:
        await run_owned_execution(runtime_request(run, agent, conversation), operation)
    assert caught.value.status == "owner_changed"
    assert get_run_task_registry().get(run.id) is None


async def _api_inputs(
    run: ConversationRun, agent: Agent, conversation: Conversation
) -> tuple[SimpleNamespace, SimpleNamespace]:
    async with TestSession() as db:
        row = await db.get(ConversationRun, run.id)
        assert row is not None
        row.status = "running"
        row.worker_instance_id = get_run_task_registry().worker_instance_id
        await db.commit()
    request = runtime_request(run, agent, conversation)
    execution = SimpleNamespace(
        conversation_run_id=request.run_id,
        conversation_id=request.conversation_id,
        root_agent_id=request.root_agent_id,
        context=request.context,
        public_run_id="api-run",
    )
    return execution, SimpleNamespace(handle=SimpleNamespace(digest="d"), cfg=None)


@pytest.mark.asyncio
async def test_api_owner_changed_is_catchable_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run, agent, conversation = await seed_runtime_run()
    execution, prepared = await _api_inputs(run, agent, conversation)
    monkeypatch.setattr(
        prepared_execution,
        "SEAMS",
        replace(prepared_execution.SEAMS, run_root_base=tmp_path),
    )

    async def invoke(*_args: Any, **_kwargs: Any) -> str:
        await _steal_ownership(run.id)
        return "done"

    monkeypatch.setattr(agent_stream_runner, "execute_prepared_agent_invoke", invoke)
    with pytest.raises(RuntimeOwnershipConflict) as caught:
        await prepared_execution.execute_prepared_wait(execution, prepared)  # type: ignore[arg-type]
    assert caught.value.status == "owner_changed"
    assert get_run_task_registry().get(run.id) is None
