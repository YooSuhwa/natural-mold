"""Triggers and the Agent API run with the company guardrail policy (plan §3.4)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import pytest

from app.authz.conversation_scope import require_conversation_agent_id
from app.dependencies import CurrentUser
from app.guardrails.pinning import RUN_METADATA_KEY
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.organization import Tenant
from app.services import conversation_run_service, conversation_run_worker
from tests.authz.run_fixture_config import run_fixture_config
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent
from tests.test_agent_lifecycle_trigger_runtime import _seed_trigger
from tests.test_agent_runtime_public_api import _seed_deployed_agent_with_key

POLICY = {"mode": "enforced", "default_template": "builtin:kr-observe", "locked_rules": []}


async def _enforce(tenant_id: Any = TEST_TENANT_ID) -> None:
    async with TestSession() as db:
        tenant = await db.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.settings = {**(tenant.settings or {}), "guardrails": POLICY}
        await db.commit()


@pytest.mark.asyncio
async def test_a_trigger_run_gets_the_company_policy() -> None:
    trigger_id, _ = await _seed_trigger()
    await _enforce()
    seen: list[Any] = []

    async def invoke(cfg: Any, *_args: Any, **_kwargs: Any) -> str:
        seen.append(cfg)
        return "done"

    with (
        patch("app.agent_runtime.trigger_executor.async_session", TestSession),
        patch("app.agent_runtime.trigger_executor.execute_agent_invoke", invoke),
    ):
        from app.agent_runtime.trigger_executor import execute_trigger

        await execute_trigger(str(trigger_id), force=True)

    [cfg] = seen
    assert cfg.guardrail_resolved
    assert cfg.guardrail_rules.template == "builtin:kr-observe"


@pytest.mark.anyio
async def test_an_agent_api_run_gets_the_company_policy(client, db, monkeypatch) -> None:
    agent, deployment, _key, cleartext = await _seed_deployed_agent_with_key(db)
    await db.commit()
    await _enforce(agent.tenant_id)
    seen: list[Any] = []

    async def invoke(cfg: Any, *_args: Any, **_kwargs: Any) -> str:
        seen.append(cfg)
        return "answer"

    monkeypatch.setattr("app.agent_api.runtime_service.execute_agent_invoke", invoke)

    response = await client.post(
        "/v1/runs/wait",
        headers={"Authorization": f"Bearer {cleartext}"},
        json={
            "agent_id": deployment.public_id,
            "input": {"messages": [{"role": "user", "content": "Hello"}]},
        },
    )

    assert response.status_code == 200, response.text
    [cfg] = seen
    assert cfg.guardrail_resolved
    assert cfg.guardrail_rules.template == "builtin:kr-observe"


async def _chat_run(conversation_id: Any) -> Any:

    async with TestSession() as db:
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        run = await conversation_run_service.create_run(
            db,
            conversation_id=conversation_id,
            agent_id=require_conversation_agent_id(conversation),
            context=unit_context(TEST_USER_ID),
            source="chat",
            input_preview="hi",
        )
        await db.commit()
        return run.id


@pytest.mark.asyncio
async def test_a_chat_run_gets_and_pins_the_company_policy() -> None:

    conversation_id = await seed_conversation_with_agent()
    run_id = await _chat_run(conversation_id)
    await _enforce()
    cfg = await run_fixture_config(run_id, TestSession)
    seen: list[Any] = []

    async def capture(run_cfg: Any, _payload: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        seen.append(run_cfg)
        if False:  # pragma: no cover - async generator shape
            yield ""

    registry = conversation_run_worker.RunTaskRegistry(worker_instance_id="guardrail-worker")
    await conversation_run_worker.start_conversation_run(
        run_id=run_id,
        conversation_id=conversation_id,
        cfg=cfg,
        user=CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test"),
        input_payload={"messages": [{"type": "human", "content": "hi"}]},
        moldy_source="chat",
        executor_fn=capture,
        registry=registry,
    )
    task = registry.get(run_id)
    assert task is not None
    await task

    [run_cfg] = seen
    assert run_cfg.guardrail_resolved
    assert run_cfg.guardrail_rules.template == "builtin:kr-observe"
    async with TestSession() as db:
        run = await db.get(ConversationRun, run_id)
        assert run is not None and run.metadata_json is not None
        assert run.metadata_json[RUN_METADATA_KEY]["rules"]["template"] == "builtin:kr-observe"
