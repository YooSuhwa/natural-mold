"""F-06 M1: a prepared lease decides before its locks and never calls OpenFGA under them."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.agent_stream_runner import execute_agent_stream
from app.agent_runtime.prepared_run_input_policy import PreparedRunUnsupportedError
from app.authz import runtime
from app.authz.principal import Principal
from app.authz.run_decisions import batch_decide, fresh_run_decision_scope
from app.authz.service import AuthzService
from app.models.agent import Agent
from app.models.tool import AgentToolLink, Tool
from app.services import agent_execution_runtime
from tests.authz import prepared_adapter_testkit as akit
from tests.authz import prepared_entry_testkit as ekit
from tests.authz import prepared_materialization_testkit as kit
from tests.authz.authz_service_testkit import RecordingClient


class LockAwareClient(RecordingClient):
    """Records every OpenFGA query with whether the lease's locked resolver was running."""

    def __init__(self, allowed: frozenset[tuple[str, str, str]]) -> None:
        super().__init__(allowed=allowed)
        self.locked = False
        self.events: list[bool] = []

    async def check(self, query: Any, **kwargs: Any) -> bool:
        self.events.append(self.locked)
        return await super().check(query, **kwargs)

    async def batch_check(self, queries: Any, **kwargs: Any) -> tuple[bool, ...]:
        self.events.extend(self.locked for _ in queries)
        return await super().batch_check(queries, **kwargs)


def _mark_locked_resolver(monkeypatch: pytest.MonkeyPatch, client: LockAwareClient) -> None:
    original = agent_execution_runtime.materialize_and_activate

    async def materialize(db: Any, target: Any, *, prepare_inputs: Any, **kwargs: Any) -> Any:
        async def locked(session: Any, run: Any) -> Any:
            client.locked = True  # inside the gateway/Run/Conversation/lifecycle locks
            try:
                return await prepare_inputs(session, run)
            finally:
                client.locked = False

        return await original(db, target, prepare_inputs=locked, **kwargs)

    monkeypatch.setattr(agent_execution_runtime, "materialize_and_activate", materialize)


async def test_a_prepared_chat_run_decides_before_the_locks(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = await ekit.setup(db, tmp_path, monkeypatch, [AIMessage("done")])
    context = harness.seeded.graph.context
    me = f"user:{context.user_id}"
    models = await db.scalars(select(Agent.model_id).where(Agent.user_id == context.user_id))
    client = LockAwareClient(frozenset((me, "can_use", f"llm_model:{m}") for m in models))
    _mark_locked_resolver(monkeypatch, client)
    cfg = await ekit.entry_cfg(harness)  # the entry route's own (unlocked) preparation
    client.events.clear()
    monkeypatch.setattr(runtime, "_current", AuthzService(client, mode="enforce"))  # type: ignore[arg-type]
    payload = [{"role": "user", "content": "hi"}]
    await ekit.run_through_worker(
        harness, harness.seeded.run_id, cfg, payload, execute_agent_stream
    )
    assert len(harness.leases) == 1
    assert client.events and not any(client.events)  # decided, and never under the locks


async def test_a_sealed_memo_answers_misses_with_no_and_no_call() -> None:
    client = RecordingClient()
    authz = AuthzService(client, mode="enforce")  # type: ignore[arg-type]
    p = Principal("user", uuid.UUID(int=0), uuid.UUID(int=1))
    with fresh_run_decision_scope() as memo:
        await batch_decide(authz, p, [("can_use", "tool:a")])
        memo.seal()
        asked = len(client.queries)
        answers = await batch_decide(authz, p, [("can_use", "tool:a"), ("can_use", "tool:b")])
    assert answers == {("can_use", "tool:a"): False, ("can_use", "tool:b"): False}
    assert len(client.queries) == asked and memo.asked_after_seal == 1


async def test_legacy_prepared_children_check_unsupported_shapes_before_credentials(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L4: a child's unsupported shape fails before its tools resolve any connection."""
    kit.fresh_registry(monkeypatch)
    akit.patch_keys(monkeypatch)
    seeded = await akit.seed(db)
    child = await db.get(Agent, seeded.child_agent_id)
    assert child is not None
    child.middleware_configs = [{"type": "model_retry", "params": {}}]
    ctx = seeded.graph.context
    tool = Tool(
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        tenant_id=ctx.tenant_id,
        definition_key="http_request",
        name="per-user",
        parameters={},
        credential_mode="per_user",
        credential_definition_key="http_bearer",
    )
    db.add(tool)
    await db.flush()
    db.add(AgentToolLink(agent_id=child.id, tool_id=tool.id))
    await db.commit()
    prepare = akit.resolver(seeded, tmp_path, akit.Memory())
    scripts = {seeded.root_model: [AIMessage("x")], seeded.child_model: []}
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        statements.append(statement)

    engine = db.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", record)
    try:
        with pytest.raises(PreparedRunUnsupportedError) as error:
            await akit.materialize(seeded, prepare, scripts, kit.FakeMcpServer())
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert error.value.code.endswith(f"child:{seeded.child_name}:middleware:model_retry")
    assert not any("resource_credential_bindings" in sql for sql in statements)
