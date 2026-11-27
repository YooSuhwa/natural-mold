"""Replay an unmodified, paused Deep Agents 0.7.32 child with only the current stack.

The archive contains PostgreSQL rows from the historical graph and Moldy writer.
The captured v3 control finished that graph once. The current route must refuse
a revoked link, then finish the original tool exactly once after it is restored.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from ag_ui.core import ResumeEntry
from langgraph.checkpoint.base import copy_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from app.agent_runtime import checkpointer
from app.agent_runtime.protocol_redaction import REDACTED_SENSITIVE_FIELD
from app.dependencies import CurrentUser
from app.exceptions import ConflictError
from app.models.agent import Agent
from app.models.agent_subagent import AgentSubAgentLink
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.services.ag_ui_resume import verify_ag_ui_resume
from tests.authz.unit_scope_fixture import ensure_unit_scope, unit_context
from tests.integration.ag_ui_legacy_checks import OldRun, assert_standard_resume
from tests.integration.test_conversation_queue_concurrency import _postgres_session_factory

pytestmark = pytest.mark.integration
_ARCHIVE = Path(__file__).parents[1] / "fixtures/ag_ui_v1/legacy_delegated_hitl_032.json.gz"
_TABLES = (
    "models",
    "agents",
    "agent_subagents",
    "conversations",
    "conversation_runs",
    "conversation_run_agents",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


async def _restore_rows(connection: psycopg.AsyncConnection[Any], archive: dict[str, Any]) -> None:
    # The original writer predates some current columns. Insert exactly the old
    # columns, allowing ordinary PostgreSQL migration defaults to populate newer ones.
    for table in _TABLES:
        rows = archive[table]
        assert rows, f"Missing old rows for {table}"
        columns = list(rows[0])
        assert all(set(row) == set(columns) for row in rows)
        query = sql.SQL(
            "INSERT INTO {table} ({cols}) "
            "SELECT {cols} FROM jsonb_populate_recordset(NULL::{table}, %s)"
        ).format(
            table=sql.Identifier(table),
            cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        await connection.execute(query, (Jsonb(rows),))


@asynccontextmanager
async def _archived_run() -> AsyncIterator[OldRun]:
    with gzip.open(_ARCHIVE, "rt", encoding="utf-8") as stream:
        archive = json.load(stream)
    tables = archive["tables"]
    encoded = json.dumps(tables, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    assert hashlib.sha256(encoded).hexdigest() == archive["tablesSha256"]
    assert archive["source"]["deepagents"] == "0.7.32"
    assert archive["source"]["langgraph"] == "1.2.19"
    assert archive["source"]["commit"] == "631ae500aa73efcd3897f906799725faeba5a999"
    assert (
        archive["tablesSha256"]
        == "e33fcab4c15017b619a7b4dad90d2a1771d4a154bb3b0d7635dffe32a45f49a9"
    )
    assert archive["v3"]["accepted"] and archive["v3"]["effects"] == 1
    assert archive["v3"]["pending"] == 0
    assert not tables.get("approval_requests") and not tables.get("message_events")
    scope = archive["scope"]
    owner = UUID(scope["userId"])
    context = unit_context(owner)
    assert (str(context.org_id), str(context.tenant_id)) == (scope["orgId"], scope["tenantId"])
    conversation_id = UUID(scope["conversationId"])
    root_id, child_id = UUID(scope["rootAgentId"]), UUID(scope["childAgentId"])
    model_id = UUID(scope["modelId"])
    sessions, engine = _postgres_session_factory()
    url = make_url(os.environ["INTEGRATION_DATABASE_URL"]).set(drivername="postgresql")
    dsn = url.render_as_string(hide_password=False)
    try:
        async with (
            await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection,
            AsyncPostgresSaver.from_conn_string(dsn) as saver,
        ):
            # IDs remain byte-identical to the archived graph. Serialize only this
            # fixture when two test processes use the same disposable database.
            await connection.execute(
                "SELECT pg_advisory_lock(hashtext('moldy-agui-archived-child-032'))"
            )
            await saver.setup()
            async with sessions.begin() as db:
                assert await db.get(User, owner) is None, "Archive owner already exists"
                await ensure_unit_scope(
                    db,
                    CurrentUser(id=owner, email="archive-child-032@test.invalid", name="Fixture"),
                )
            restored = False
            try:
                async with connection.transaction():
                    await _restore_rows(connection, tables)
                restored = True
                yield OldRun(
                    sessions,
                    dsn,
                    owner,
                    conversation_id,
                    root_id,
                    child_id,
                    scope["childName"],
                    UUID(scope["runId"]),
                    archive["issued"],
                )
            finally:
                if restored:
                    await saver.adelete_thread(str(conversation_id))
                async with sessions.begin() as db:
                    if restored:
                        await db.execute(
                            delete(Conversation).where(Conversation.id == conversation_id)
                        )
                        await db.execute(delete(Agent).where(Agent.id.in_((root_id, child_id))))
                        await db.execute(delete(Model).where(Model.id == model_id))
                    await db.execute(delete(User).where(User.id == owner))
    finally:
        await engine.dispose()


async def _deny_unselected_branch(case: OldRun, patch: pytest.MonkeyPatch) -> None:
    thread = str(case.conversation_id)
    async with AsyncPostgresSaver.from_conn_string(case.dsn) as saver:
        patch.setattr(checkpointer, "_checkpointer", saver)
        head = await saver.aget_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
        assert (
            head and head.parent_config and head.checkpoint["id"] == case.issued["rootCheckpointId"]
        )
        parent = await saver.aget_tuple(head.parent_config)
        assert parent
        # Materialize a second, genuine root-namespace child of the same parent.
        # Leave the archived pending checkpoint as the native thread's latest head.
        sibling = copy_checkpoint(parent.checkpoint)
        sibling["id"] = str(UUID(int=UUID(head.checkpoint["id"]).int - 1))
        assert parent.checkpoint["id"] < sibling["id"] < head.checkpoint["id"]
        await saver.aput(parent.config, sibling, parent.metadata, {})
        context = unit_context(case.user_id)
        async with case.db.begin() as db:
            conv = await db.get(Conversation, case.conversation_id)
            assert conv
            conv.active_branch_checkpoint_id = sibling["id"]
        async with case.db() as db:
            conv = await db.get(Conversation, case.conversation_id)
            assert conv
            with pytest.raises(ConflictError) as stale:
                await verify_ag_ui_resume(
                    db,
                    conversation=conv,
                    context=context,
                    entries=[
                        ResumeEntry(
                            interrupt_id=case.issued["interruptId"],
                            status="resolved",
                            payload={"decisions": [{"type": "approve"}]},
                        )
                    ],
                )
            assert stale.value.code == "STALE_INTERRUPT"
        async with case.db.begin() as db:
            conv = await db.get(Conversation, case.conversation_id)
            assert conv
            conv.active_branch_checkpoint_id = case.issued["rootCheckpointId"]


@pytest.mark.asyncio
async def test_archived_delegated_approval_obeys_current_grant_and_resumes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _archived_run() as case:
        with monkeypatch.context() as branch:
            await _deny_unselected_branch(case, branch)
        with monkeypatch.context() as revoked:
            await assert_standard_resume(case, revoked=True, monkeypatch=revoked)
        # Restore the same delegation, leaving the original checkpoint, run,
        # participants and unresolved decision exactly as the old writer persisted them.
        async with case.db.begin() as db:
            assert (
                await db.scalar(
                    select(ApprovalRequest.id).where(ApprovalRequest.run_id == case.run_id)
                )
                is None
            )
            assert (
                await db.scalar(
                    select(AgentSubAgentLink.parent_agent_id).where(
                        AgentSubAgentLink.parent_agent_id == case.agent_id
                    )
                )
                is None
            )
            db.add(
                AgentSubAgentLink(
                    parent_agent_id=case.agent_id,
                    sub_agent_id=case.child_id,
                    delegated_by=case.user_id,
                )
            )
        await assert_standard_resume(case, revoked=False, monkeypatch=monkeypatch)
        async with case.db() as db:
            claim = await db.scalar(
                select(ApprovalRequest).where(ApprovalRequest.run_id == case.run_id)
            )
            assert claim and (claim.status, claim.decision_no, claim.decisions) == (
                "approved",
                1,
                ["approve"],
            )
            assert claim.actions == [
                {
                    "name": REDACTED_SENSITIVE_FIELD,
                    "args": REDACTED_SENSITIVE_FIELD,
                    "description": None,
                    "allowed_decisions": ["approve"],
                }
            ]
            child = await db.scalar(
                select(ConversationRun).where(ConversationRun.parent_run_id == case.run_id)
            )
            assert child and claim.resumed_run_id == child.id
