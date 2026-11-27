"""Read an archived Deep Agents 0.7.32 child approval without resuming the graph."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import delete

from app.agent_runtime import checkpointer
from app.models.agent_subagent import AgentSubAgentLink
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_read_pending import read_proven_interrupts
from tests.authz.unit_scope_fixture import unit_context
from tests.integration.test_ag_ui_archived_child_approval import _archived_run

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_old_child_capability_tracks_current_root_and_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with (
        _archived_run() as case,
        AsyncPostgresSaver.from_conn_string(case.dsn) as saver,
        case.db() as db,
    ):
        monkeypatch.setattr(checkpointer, "_checkpointer", saver)
        scope = unit_context(case.user_id)
        ident = case.issued["interruptId"]
        namespace = tuple(case.issued["namespace"])
        root_id = case.issued["rootCheckpointId"]
        conversation = await db.get(Conversation, case.conversation_id)
        run = await db.get(ConversationRun, case.run_id)
        assert conversation is not None and run is not None
        root = await saver.aget_tuple(
            {"configurable": {"thread_id": str(case.conversation_id), "checkpoint_ns": ""}}
        )
        assert root is not None and root.checkpoint["id"] == root_id and root.parent_config
        previous = root.parent_config["configurable"]["checkpoint_id"]
        stale = await read_proven_interrupts(db, conversation, scope, run, checkpoint_id=previous)
        assert stale.checkpoint_id is None and not stale.interrupts and not stale.actionable

        proven = await read_proven_interrupts(db, conversation, scope, run, checkpoint_id=root_id)
        assert proven.checkpoint_id == root_id and proven.actionable
        assert proven.legacy_child_ids == {ident}
        assert proven.legacy_namespaces == {ident: namespace}
        assert [slot["id"] for slot in proven.interrupts] == [ident]
        assert tuple(proven.interrupts[0]["ns"])[: len(namespace)] == namespace
        assert proven.legacy_bindings[ident].tool_call_id == case.issued["toolCallId"]

        async with case.db.begin() as writer:
            await writer.execute(
                delete(AgentSubAgentLink).where(
                    AgentSubAgentLink.parent_agent_id == case.agent_id,
                    AgentSubAgentLink.sub_agent_id == case.child_id,
                )
            )
        denied = await read_proven_interrupts(db, conversation, scope, run, checkpoint_id=root_id)
        assert not denied.actionable
        assert not denied.legacy_child_ids and not denied.legacy_namespaces
        assert not denied.interrupts and not denied.expires_at

        same_root = await saver.aget_tuple(
            {"configurable": {"thread_id": str(case.conversation_id), "checkpoint_ns": ""}}
        )
        assert same_root is not None and same_root.config == root.config
        assert same_root.pending_writes == root.pending_writes
