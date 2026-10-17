"""I-06: runs stamp their company into checkpoint metadata; the audit compares it."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypedDict
from unittest.mock import MagicMock, patch

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.tenancy.checkpoint_audit import (
    CHECKPOINT_TENANT_KEY,
    CheckpointTenantReport,
    ThreadTenant,
    audit_checkpoint_tenants,
    run_configurable,
)
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TestSession
from tests.test_executor import _cfg

A, B = str(uuid.uuid4()), str(uuid.uuid4())


class _State(TypedDict):
    value: int


def _step(state: _State) -> _State:
    return {"value": state["value"] + 1}


def test_langgraph_copies_the_run_company_into_checkpoint_metadata() -> None:
    graph = StateGraph(_State)
    graph.add_node("step", _step)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)
    saver = InMemorySaver()
    configurable = run_configurable("thread-1", uuid.uuid4(), TEST_TENANT_ID)
    graph.compile(checkpointer=saver).invoke({"value": 1}, {"configurable": configurable})
    recorded = {c.metadata.get(CHECKPOINT_TENANT_KEY) for c in saver.list(None)}
    assert recorded == {str(TEST_TENANT_ID)}


@patch("app.agent_runtime.checkpointer.get_checkpointer")
@patch("app.agent_runtime.runtime_component_builder.build_agent")
@patch("app.agent_runtime.runtime_component_builder.convert_to_langchain_messages")
@patch("app.agent_runtime.runtime_component_builder.create_chat_model")
async def test_legacy_run_config_carries_the_run_company(
    model: MagicMock, convert: MagicMock, build: MagicMock, saver: MagicMock
) -> None:
    from app.agent_runtime import runtime_component_builder as executor

    convert.return_value = [HumanMessage(content="hello")]
    _agent, _messages, config = await executor._prepare_agent(
        _cfg(thread_id="thread-tenant"), messages_history=[{"role": "user", "content": "hello"}]
    )
    assert config["configurable"][CHECKPOINT_TENANT_KEY] == str(TEST_TENANT_ID)


def test_report_separates_mismatch_unattributed_and_orphaned() -> None:
    report = CheckpointTenantReport()
    report.add(
        [
            ThreadTenant("t-ok", A, A, 3),
            ThreadTenant("t-bad", B, A, 2),
            ThreadTenant("t-bad", A, A, 1),
            ThreadTenant("t-old", None, A, 4),
            ThreadTenant("t-builder", A, None, 5),
        ]
    )
    assert (report.threads, report.checkpoints) == (4, 15)
    assert (report.mismatched, report.unattributed, report.orphaned) == (2, 4, 5)
    assert report.mismatched_threads == ["t-bad"]


def test_report_samples_at_most_twenty_threads() -> None:
    report = CheckpointTenantReport()
    for page in range(3):
        report.add([ThreadTenant(f"t{page}-{i:02}", B, A, 1) for i in range(10)])
    assert report.mismatched == 30 and len(report.mismatched_threads) == 20


async def test_audit_is_empty_off_postgres() -> None:
    async with TestSession() as db:
        report = await audit_checkpoint_tenants(db)
    assert report == CheckpointTenantReport()


class _Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class _Pool:
    def __init__(self, row: tuple[object, ...] | None, error: Exception | None = None) -> None:
        self.row, self.error = row, error

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_Pool]:
        yield self

    async def execute(self, _sql: str) -> _Cursor:
        if self.error is not None:
            raise self.error
        return _Cursor(self.row)


async def test_checkpointer_setup_is_skipped_once_every_migration_is_recorded() -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import errors

    from app.agent_runtime.checkpointer import _needs_setup

    latest = len(AsyncPostgresSaver.MIGRATIONS) - 1
    assert await _needs_setup(_Pool((latest,))) is False  # type: ignore[arg-type]
    assert await _needs_setup(_Pool((latest - 1,))) is True  # type: ignore[arg-type]
    assert await _needs_setup(_Pool((None,))) is True  # type: ignore[arg-type]
    missing = _Pool(None, errors.UndefinedTable("checkpoint_migrations"))
    assert await _needs_setup(missing) is True  # type: ignore[arg-type]
