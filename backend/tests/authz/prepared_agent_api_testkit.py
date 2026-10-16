"""Seed an accepted Agent API run for prepared-lease entry tests (internal I17).

Reuses the prepared-adapter rows (root + stored child + MCP link + snapshotted
conversation), retires the testkit's chat run, then deploys the root, mints an
API key and accepts the run through the real ``create_run_row`` writer. The
materializer seams (scripted permit-guarded models, fake MCP, fake memory) are
installed on ``prepared_execution.SEAMS``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import prepared_execution, runtime_service, service
from app.agent_api.dependencies import ApiKeyPrincipal
from app.agent_api.run_execution import ApiOwnedExecution
from app.agent_runtime import agent_stream_runner
from app.agent_runtime.message_utils import RuntimeMessage
from app.config import settings
from app.models.agent_api import AgentApiKey, AgentApiRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.schemas.agent_api import AgentApiKeyCreate, AgentDeploymentCreate
from app.services import agent_invocation_service
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_materialization_testkit as kit
from tests.conftest import TestSession

MESSAGES: list[RuntimeMessage] = [{"role": "user", "content": "hi"}]


@dataclass
class ApiRun:
    seeded: akit.Seeded
    execution: ApiOwnedExecution
    key: AgentApiKey
    factory: kit.ScriptedFactory
    server: kit.FakeMcpServer
    cleartext: str


def enable(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", enabled)


async def _root_key(*_a: Any, **_k: Any) -> str:
    return "sk-root"


async def seed_api_run(
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scripts: Callable[[akit.Seeded], dict[str, list[AIMessage]]] | None = None,
    *,
    mode: str = "wait",
    factory: type[kit.ScriptedFactory] = kit.ScriptedFactory,
    with_child: bool = True,
) -> ApiRun:
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    monkeypatch.setattr(agent_invocation_service, "resolve_llm_api_key_for_agent", _root_key)
    seeded = await akit.seed(db, with_child=with_child)
    chat_run = await db.get(ConversationRun, seeded.run_id)
    assert chat_run is not None
    chat_run.status, chat_run.is_active = "completed", False
    await db.commit()
    ctx = seeded.graph.context
    deployment = await service.create_deployment(
        db, ctx, AgentDeploymentCreate(agent_id=seeded.graph.agent_id)
    )
    key, cleartext = await service.create_api_key(
        db, ctx, AgentApiKeyCreate(name="prepared", deployment_ids=[deployment.id])
    )
    principal = ApiKeyPrincipal(key=key, user_id=ctx.user_id, context=ctx)
    conversation = await db.get(Conversation, seeded.graph.conversation_ids[0])
    assert conversation is not None
    cfg = await runtime_service.build_config_for_run(
        db,
        principal=principal,
        deployment=deployment,
        conversation=conversation,
        external_user=None,
        required_scope="invoke" if mode == "wait" else "stream",
        include_subagents=False,
    )
    row = await runtime_service.create_run_row(
        db,
        principal=principal,
        deployment=deployment,
        conversation_id=conversation.id,
        thread_id=None,
        mode=mode,
        input_payload={"messages": MESSAGES},
        metadata=None,
    )
    assert row.conversation_run_id is not None and row.conversation_id is not None
    execution = ApiOwnedExecution(
        conversation_run_id=row.conversation_run_id,
        conversation_id=row.conversation_id,
        root_agent_id=seeded.graph.agent_id,
        context=ctx,
        public_run_id=row.public_id,
        config=cfg,
        messages=list(MESSAGES),
    )
    script = scripts(seeded) if scripts else {seeded.root_model: [AIMessage("done")]}
    built = factory({seeded.child_model: [], **script})
    server = kit.FakeMcpServer()
    monkeypatch.setattr(
        prepared_execution,
        "SEAMS",
        prepared_execution.PreparedApiSeams(
            materializer=kit.seams(built, server),
            memory=akit.Memory().reads(),
            run_root_base=tmp_path / "prepared-api",
        ),
    )
    return ApiRun(seeded, execution, key, built, server, cleartext)


@dataclass(frozen=True)
class Rows:
    run: ConversationRun
    envelopes: list[AgentApiRun]


async def rows(api: ApiRun) -> Rows:
    async with TestSession() as check:
        run = await check.get(ConversationRun, api.execution.conversation_run_id)
        envelopes = list(
            await check.scalars(
                select(AgentApiRun).where(
                    AgentApiRun.conversation_run_id == api.execution.conversation_run_id
                )
            )
        )
    assert run is not None
    return Rows(run, envelopes)


def forbid_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("legacy preparation/invoke on the prepared path")

    monkeypatch.setattr(agent_stream_runner, "_prepare_agent", forbidden)
    monkeypatch.setattr(runtime_service, "execute_agent_invoke", forbidden)
    monkeypatch.setattr(runtime_service, "execute_agent_stream", forbidden)
