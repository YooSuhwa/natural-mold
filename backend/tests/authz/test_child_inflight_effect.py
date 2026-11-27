"""Cancellation and authorization loss at different boundaries of one real child tool."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import delegated_child_control
from app.agent_runtime.identity import make_agent_runtime_name
from app.authz import child_task_authorization, child_tasks_api
from app.authz.org_context import OrgContext
from app.exceptions import AppError
from app.models.agent_subagent import AgentSubAgentLink
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.authz_service_testkit import RecordingClient, service
from tests.authz.test_child_followup_runtime import (
    ResearchModel,
    SessionScope,
    child_graph,
    parallel_root,
)
from tests.authz.test_child_followup_runtime import scoped_session as scoped_session
from tests.authz.test_child_task_authorization import ChildCase
from tests.authz.test_child_task_authorization import child_case as child_case


@pytest.mark.parametrize("prepared", [False, True], ids=["ordinary", "prepared"])
@pytest.mark.parametrize("change", ["unlink", "outage", "cancel"])
async def test_authorization_loss_fences_next_dispatch_and_explicit_cancel_interrupts(
    child_case: ChildCase,
    scoped_session: SessionScope,
    monkeypatch: pytest.MonkeyPatch,
    prepared: bool,
    change: Literal["unlink", "outage", "cancel"],
) -> None:
    accepted, allow_receipt, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    effects: list[str] = []

    @tool("echo")
    async def submit(source: str) -> str:
        """Submit a simulated effect and wait for its acknowledgement."""
        effects.append(f"sent:{source}")
        accepted.set()
        try:
            await allow_receipt.wait()
        except asyncio.CancelledError:
            interrupted.set()
            raise
        effects.append(f"ack:{source}")
        return f"receipt {source}"

    # Observe progress through two complete worker polls after the command commits.
    # The graph is parked in submit; no other runtime code is opening DB sessions.
    changed = False
    remaining_polls = 2
    polled = asyncio.Event()

    @asynccontextmanager
    async def observe_poll(ctx: OrgContext) -> AsyncIterator[AsyncSession]:
        nonlocal remaining_polls
        async with scoped_session(ctx) as session:
            after_change = changed
            try:
                yield session
            finally:
                if after_change:
                    remaining_polls -= 1
                    if remaining_polls == 0:
                        polled.set()

    monkeypatch.setattr(delegated_child_control, "tenant_session", observe_poll)
    monkeypatch.setattr(delegated_child_control, "_CHECK_INTERVAL", 0.01)
    ctx = child_case.graph.context
    conversation_id = child_case.graph.conversation_ids[0]
    model = ResearchModel(responses=[])
    root = parallel_root(make_agent_runtime_name(child_case.child_id), ("alpha",))
    graph = child_graph(child_case, model, submit, InMemorySaver(), prepared, root_model=root)
    config = {
        "configurable": run_configurable(str(conversation_id), child_case.run_id, ctx.tenant_id)
    }
    run = asyncio.create_task(graph.ainvoke({"messages": [("user", "Submit once")]}, config))
    client = RecordingClient(unavailable=True)
    milestone = asyncio.create_task((interrupted if change == "cancel" else polled).wait())
    try:
        async with asyncio.timeout(15):
            await accepted.wait()
            if change == "unlink":
                async with scoped_session(ctx) as db:
                    await db.execute(
                        delete(AgentSubAgentLink).where(
                            AgentSubAgentLink.parent_agent_id == child_case.graph.agent_id,
                            AgentSubAgentLink.sub_agent_id == child_case.child_id,
                        )
                    )
                    await db.commit()
            elif change == "outage":
                monkeypatch.setattr(
                    child_task_authorization, "current_authz", lambda: service(client)
                )
            else:
                async with scoped_session(ctx) as db:
                    page = await child_tasks_api.list_child_tasks(
                        db, conversation_id, ctx, limit=2, offset=0
                    )
                    [task] = page.items
                    await child_tasks_api.cancel_child(db, conversation_id, task.id, ctx)
            changed = True

            done, _ = await asyncio.wait((run, milestone), return_when=asyncio.FIRST_COMPLETED)
            assert milestone in done, (
                "The worker interrupted the admitted tool before acknowledgement"
            )
            if change != "cancel":
                assert not interrupted.is_set()
                assert not run.done()
                allow_receipt.set()
            try:
                result = await run
            except AppError as exc:
                assert change == "outage" and exc.status == 503
            else:
                assert [m.status for m in result["messages"] if isinstance(m, ToolMessage)] == [
                    "error"
                ]
    finally:
        allow_receipt.set()
        milestone.cancel()
        run.cancel()
        await asyncio.gather(run, milestone, return_exceptions=True)

    assert effects == (["sent:alpha"] if change == "cancel" else ["sent:alpha", "ack:alpha"])
    assert len(model._history["alpha"]) == 1
    if change == "outage":
        assert client.queries
