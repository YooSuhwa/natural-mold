"""Ordinary seam matrix: authority revocation beats an exact archived lease (I23).

Each case runs a real seeded chat run through the owned worker with the lease
flag on. Right after one leaf's permit closes, another session archives the
organization (raw stamp, isolating the permit) and applies one authority
revocation. The very next dispatch — tool, child launch, or provider send —
must be refused with the exact code, terminalizing the run with frozen fields
and no further send, hydration or participant growth. Archive alone is
bypassed by the exact lease and the run completes. PostgreSQL row-lock
barriers for the same matrix live in ``test_task10_authority_integration``.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.agent_stream_runner import execute_agent_stream
from app.agent_runtime.identity import make_agent_runtime_name
from app.agent_runtime.prepared_run_registry import get_prepared_run_registry
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.task10_runtime_barriers import (
    MUTATIONS,
    Barriers,
    Hook,
    archived_org,
    install_chat,
    tool_call,
)
from tests.conftest import TestSession

PAYLOAD = [{"role": "user", "content": "hi"}]


def _first(harness: ekit.Harness, dispatch: str) -> AIMessage:
    if dispatch == "tool":
        return tool_call("ls", {"path": "/"}, "ls-1")
    return kit.task_call(harness.seeded.child_name, "task-1")


def _revoke(harness: ekit.Harness, mutation: str | None) -> Hook:
    context = harness.seeded.graph.context

    async def revoke() -> None:
        async with TestSession() as tx:
            await tx.execute(archived_org(context))
            if mutation is not None:
                await tx.execute(MUTATIONS[mutation][0](context))
            await tx.commit()

    return revoke


async def _run(harness: ekit.Harness, factory: Barriers, monkeypatch: pytest.MonkeyPatch):
    install_chat(monkeypatch, harness, factory)
    cfg = await ekit.entry_cfg(harness)
    harness.resolutions.clear()
    return await ekit.run_through_worker(
        harness, harness.seeded.run_id, cfg, PAYLOAD, execute_agent_stream
    )


def _assert_no_hydration(harness: ekit.Harness, factory: Barriers) -> None:
    assert harness.resolutions == [(False, True)]
    assert harness.prepare_agent_calls == 0 and harness.server.discoveries == 1
    assert set(factory.closes().values()) == {1}
    assert get_prepared_run_registry().state(harness.seeded.run_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", list(MUTATIONS))
@pytest.mark.parametrize("dispatch", ["tool", "child", "provider"])
async def test_revocation_beats_archived_lease_at_next_dispatch(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str, dispatch: str
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [])
    seeded = harness.seeded
    root, child = seeded.root_model, seeded.child_model
    hooked = child if dispatch == "provider" else root
    factory = Barriers(
        {root: [_first(harness, dispatch), AIMessage("done")], child: [AIMessage("helped")]},
        hooks={hooked: _revoke(harness, mutation)},
    )

    row = await _run(harness, factory, monkeypatch)

    code = MUTATIONS[mutation][1]
    assert (row.status, row.is_active, row.error_code, row.error_message) == (
        "failed",
        False,
        "stream_error",
        code,
    )
    assert row.completed_at is not None and row.runtime_materialized_at is not None
    assert row.runtime_materialization_sha256 == harness.leases[-1].digest
    assert ([root, child] if dispatch == "provider" else [root]) == kit.SENDS
    _assert_no_hydration(harness, factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", ["tool", "child", "provider"])
async def test_archive_alone_is_bypassed_by_the_exact_lease(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dispatch: str
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [])
    root, child = harness.seeded.root_model, harness.seeded.child_model
    hooked = child if dispatch == "provider" else root
    factory = Barriers(
        {root: [_first(harness, dispatch), AIMessage("done")], child: [AIMessage("helped")]},
        hooks={hooked: _revoke(harness, None)},
    )

    row = await _run(harness, factory, monkeypatch)

    assert (row.status, row.error_code) == ("completed", None)
    assert ([root, root] if dispatch == "tool" else [root, child, root]) == kit.SENDS
    _assert_no_hydration(harness, factory)


@pytest.mark.asyncio
async def test_participant_expansion_after_marker_never_launches(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [])
    seeded = harness.seeded
    context, root = seeded.graph.context, seeded.root_model
    newcomer_id = uuid4()
    late = make_agent_runtime_name(newcomer_id)

    async def expand() -> None:
        async with TestSession() as tx:
            parent = await tx.get(Agent, seeded.graph.agent_id)
            assert parent is not None
            tx.add(
                Agent(
                    id=newcomer_id,
                    user_id=context.user_id,
                    org_id=context.org_id,
                    tenant_id=context.tenant_id,
                    name="Newcomer",
                    description="Joined after the marker",
                    model_id=parent.model_id,
                    system_prompt="late",
                )
            )
            await tx.flush()
            tx.add(AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=newcomer_id))
            await tx.execute(archived_org(context))
            await tx.commit()

    script = [tool_call("ls", {"path": "/"}, "ls-1"), kit.task_call(late, "task-late")]
    factory = Barriers(
        {root: [*script, AIMessage("done")], seeded.child_model: []}, hooks={root: expand}
    )

    row = await _run(harness, factory, monkeypatch)

    assert (row.status, row.error_code) == ("completed", None), row.error_message
    assert [root, root, root] == kit.SENDS
    assert all(late not in path for path in harness.leases[-1].binding.participant_paths)
    denied = [
        message
        for message in ekit.SEEN[-1]
        if isinstance(message, ToolMessage) and message.tool_call_id == "task-late"
    ]
    # The frozen catalog answers from memory: the newcomer is not a participant.
    assert len(denied) == 1 and "does not exist" in str(denied[0].content)
    assert late in str(denied[0].content)
    assert [str(m.content) for m in ekit.SEEN[0] if isinstance(m, HumanMessage)] == ["hi"]
    _assert_no_hydration(harness, factory)
