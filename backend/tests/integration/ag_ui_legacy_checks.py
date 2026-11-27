"""Assertions shared by archived and live .32-to-.42 PostgreSQL contract tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import database
from app.agent_runtime import checkpointer, langgraph_agent_stream_runner
from app.agent_runtime.runtime import usage_guard
from app.agent_runtime.runtime_config import AgentConfig
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token, create_csrf_token
from app.config import settings
from app.dependencies import get_db
from app.main import create_app
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation_run import ConversationRun
from app.routers import conversation_ag_ui_commands
from app.services import (
    ag_ui_deferred_delivery,
    conversation_run_queue_worker,
    conversation_run_worker,
    conversation_stream_service,
)
from app.services.conversation_run_task_registry import RunTaskRegistry
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.ag_ui_legacy_child_graph import build_graph, config


@dataclass
class OldRun:
    db: async_sessionmaker[AsyncSession]
    dsn: str
    user_id: UUID
    conversation_id: UUID
    agent_id: UUID
    child_id: UUID
    child_name: str
    run_id: UUID
    issued: dict[str, Any]


async def assert_standard_resume(
    case: OldRun, revoked: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid, iid, scope = (
        str(case.conversation_id),
        case.issued["interruptId"],
        unit_context(case.user_id),
    )
    async with case.db.begin() as db:
        if revoked:
            await db.execute(
                delete(AgentSubAgentLink).where(
                    AgentSubAgentLink.parent_agent_id == case.agent_id,
                    AgentSubAgentLink.sub_agent_id == case.child_id,
                )
            )
    for module in (
        database,
        usage_guard,
        conversation_run_queue_worker,
        conversation_run_worker,
        conversation_stream_service,
    ):
        monkeypatch.setattr(module, "async_session", case.db)
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", True)
    monkeypatch.setattr(settings, "prepared_run_lease_enabled", False)
    effects: list[str] = []
    async with AsyncPostgresSaver.from_conn_string(case.dsn) as saver:
        monkeypatch.setattr(checkpointer, "_checkpointer", saver)
        graph = build_graph(saver, case.child_name, effects)

        async def resume_graph(
            cfg: AgentConfig, *, stream_input: Command, run_id: str, **_kwargs: Any
        ) -> AsyncIterator[str]:
            assert cfg.checkpoint_id == case.issued["rootCheckpointId"]
            assert stream_input.resume == {iid: {"decisions": [{"type": "approve"}]}}
            durable_config = config(tid, run_id, scope.tenant_id)
            durable_config.setdefault("configurable", {})["checkpoint_id"] = cfg.checkpoint_id
            result = await graph.ainvoke(stream_input, durable_config)
            assert not result.get("__interrupt__")
            yield 'event: message_end\ndata: {"content":"Review complete.","usage":{}}\n\n'

        async def isolated_db() -> AsyncIterator[AsyncSession]:
            async with case.db() as db:
                yield db

        # Observe command authorization and the real worker independently of the
        # projection consumer; the old and new graphs share only the PostgreSQL saver.
        async def empty_attachment(*_args: object) -> StreamingResponse:
            return StreamingResponse(iter(()), media_type="text/event-stream")

        monkeypatch.setattr(
            langgraph_agent_stream_runner, "_run_langgraph_agent_stream", resume_graph
        )
        monkeypatch.setattr(conversation_ag_ui_commands, "ag_ui_run_response", empty_attachment)
        registry = RunTaskRegistry(worker_instance_id="legacy-child-compatibility")
        dispatch = ag_ui_deferred_delivery.dispatch_ag_ui_run

        async def wake(run_id: UUID) -> None:
            await dispatch(run_id, registry=registry)

        monkeypatch.setattr(ag_ui_deferred_delivery, "dispatch_ag_ui_run", wake)
        app = create_app()
        app.dependency_overrides[get_db] = isolated_db
        csrf = create_csrf_token(case.user_id)
        public_id = uuid4().hex
        request = {
            "threadId": tid,
            "runId": public_id,
            "messages": [],
            "resume": [
                {
                    "interruptId": iid,
                    "status": "resolved",
                    "payload": {"decisions": [{"type": "approve"}]},
                }
            ],
        }
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"X-CSRF-Token": csrf},
                cookies={
                    settings.cookie_name_access: create_access_token(case.user_id),
                    settings.cookie_name_csrf: csrf,
                    ACTIVE_ORG_COOKIE: str(scope.org_id),
                },
            ) as client:
                endpoint = f"/api/conversations/{tid}/ag-ui/runs"
                response = await client.post(endpoint, json=request)
                if revoked:
                    assert response.status_code in {403, 404, 409}, response.text
                else:
                    assert response.status_code == 200, response.text
                    async with case.db() as db:
                        child = await db.scalar(
                            select(ConversationRun).where(
                                ConversationRun.parent_run_id == case.run_id
                            )
                        )
                        assert child
                        task = registry.get(child.id)
                        if task:
                            await asyncio.wait_for(task, 15)
                    assert (await client.post(endpoint, json=request)).status_code == 200
        finally:
            await registry.shutdown()
        state = await graph.aget_state(config(tid, case.run_id, scope.tenant_id))
        assert [entry.id for entry in state.interrupts] == ([iid] if revoked else [])
        assert effects == ([] if revoked else ["draft"])
        async with case.db() as db:
            receipts = await db.scalar(
                select(func.count())
                .select_from(AgUiCommandReceipt)
                .where(AgUiCommandReceipt.conversation_id == case.conversation_id)
            )
            assert receipts == (0 if revoked else 1)
            children = (
                await db.scalars(
                    select(ConversationRun).where(ConversationRun.parent_run_id == case.run_id)
                )
            ).all()
            assert [child.status for child in children] == ([] if revoked else ["completed"])
