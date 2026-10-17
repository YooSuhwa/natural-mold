"""F-14 (11.10, Q8, Q22): every accepted run records who asked, who acts and who pays.

Chat (owner and shared caller), trigger and Agent API runs go through
``accept_owned_execution``; the run row and each participant (root and subagent) carry
the attribution, and the run's usage row copies it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.fake import FakeAuthz
from app.authz.service import AuthzService
from app.dependencies import CurrentUser
from app.models.agent import Agent
from app.models.agent_api import AgentApiRun
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.agent_trigger_run import AgentTriggerRun
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.token_usage import TokenUsage
from app.services import agent_execution_owner
from app.services.agent_execution_owner import ExecutionEnvelopeRef, OwnedExecutionRequest
from app.services.conversation_run_metrics_service import persist_run_metrics_and_usage
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    ensure_unit_scope,
    unit_context,
)
from tests.test_agent_lifecycle_run_ownership import (
    _seed_execution_tree,
    _skip_sqlite_advisory_locks,
)
from tests.test_conversation_run_metrics_service import _snapshot

pytestmark = pytest.mark.asyncio
CALLER = uuid.UUID("00000000-0000-0000-0000-00000000f014")


async def _envelope(
    db: AsyncSession, source: str, root: Agent, conversation: Conversation
) -> ExecutionEnvelopeRef | None:
    if source == "api":
        row: AgentApiRun | AgentTriggerRun = AgentApiRun(
            public_id=f"run-{uuid.uuid4()}",
            user_id=root.user_id,
            deployment_id=None,
            deployment_public_id_snapshot="d",
            conversation_id=conversation.id,
            mode="background",
            status="pending",
        )
    elif source == "trigger":
        row = AgentTriggerRun(
            trigger_id=None,
            agent_id=root.id,
            trigger_name_snapshot="t",
            user_id=root.user_id,
            conversation_id=conversation.id,
            status="queued",
            source="scheduled",
            input_message="run",
        )
    else:
        return None
    db.add(row)
    await db.flush()
    return ExecutionEnvelopeRef(source, row.id)  # type: ignore[arg-type]


async def _accept(db: AsyncSession, source: str, conversation: Conversation, user: uuid.UUID):
    root = await db.get(Agent, conversation.agent_id)
    assert root is not None
    accepted = await agent_execution_owner.accept_owned_execution(
        db,
        OwnedExecutionRequest(
            conversation_id=conversation.id,
            root_agent_id=root.id,
            context=unit_context(user),
            source=source,  # type: ignore[arg-type]
            input_preview="f14",
            envelope=await _envelope(db, source, root, conversation),
        ),
    )
    await db.commit()
    run = await db.get(ConversationRun, accepted.run_id)
    assert run is not None
    participants = {
        row.role: row
        for row in await db.scalars(
            select(ConversationRunAgent).where(ConversationRunAgent.run_id == run.id)
        )
    }
    return run, participants


def _identity(row: object) -> tuple[object, ...]:
    return tuple(
        getattr(row, name)
        for name in (
            "invoker_type",
            "resource_owner_user_id",
            "on_behalf_of_user_id",
            "credential_subject_user_id",
        )
    )


@pytest.mark.parametrize(
    ("source", "run_source", "invoker"),
    [("chat", "CHAT", "user"), ("trigger", "TRIGGER", "schedule"), ("api", "API", "api")],
)
async def test_owner_runs_record_source_scope_and_owner_identity(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, source: str, run_source: str, invoker: str
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    root, child, conversation = await _seed_execution_tree(db)
    owner = root.user_id
    run, parts = await _accept(db, source, conversation, owner)
    # The trigger owner / key owner / chat caller is the requester and the billed user.
    assert (run.org_id, run.tenant_id, run.user_id) == (TEST_ORG_ID, TEST_TENANT_ID, owner)
    assert (run.run_source, *_identity(run)) == (run_source, invoker, owner, owner, owner)
    assert _identity(parts["root"]) == (invoker, owner, owner, owner)
    assert _identity(parts["subagent"]) == ("subagent", child.user_id, owner, owner)
    await persist_run_metrics_and_usage(db, run=run, snapshot=_snapshot(), model_name="m")
    usage = await db.scalar(select(TokenUsage).where(TokenUsage.run_id == run.id))
    assert usage is not None
    assert (usage.org_id, usage.tenant_id, usage.caller_user_id) == (
        TEST_ORG_ID,
        TEST_TENANT_ID,
        owner,
    )
    assert (usage.run_source, usage.invoker_type, usage.resource_owner_user_id) == (
        run_source,
        invoker,
        owner,
    )


@pytest.mark.parametrize(("mode", "acting"), [("fixed", "owner"), ("per_user", "caller")])
async def test_shared_caller_pays_and_identity_follows_the_agent_mode(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, mode: str, acting: str
) -> None:
    await _skip_sqlite_advisory_locks(monkeypatch)
    root, child, _ = await _seed_execution_tree(db)
    owner = root.user_id
    root.identity_mode = mode
    child.identity_mode = mode
    await ensure_unit_scope(db, CurrentUser(id=CALLER, email="caller@f14.test", name="Caller"))
    conversation = Conversation(
        agent_id=root.id,
        user_id=CALLER,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        agent_name_snapshot=root.name,
        last_activity_at=root.created_at,
    )
    db.add(conversation)
    await db.commit()
    allowed = frozenset({(f"user:{CALLER}", "can_run", f"agent:{root.id}")})
    monkeypatch.setattr(runtime, "_current", AuthzService(FakeAuthz(allowed), mode="enforce"))
    run, parts = await _accept(db, "chat", conversation, CALLER)
    expected = owner if acting == "owner" else CALLER
    assert run.user_id == CALLER  # the shared caller is billed (Q8)
    assert _identity(run) == ("user", owner, expected, expected)
    assert _identity(parts["subagent"]) == ("subagent", child.user_id, expected, expected)
    await persist_run_metrics_and_usage(db, run=run, snapshot=_snapshot(), model_name="m")
    usage = await db.scalar(select(TokenUsage).where(TokenUsage.run_id == run.id))
    assert usage is not None
    assert (usage.caller_user_id, usage.resource_owner_user_id) == (CALLER, owner)
    assert (usage.on_behalf_of_user_id, usage.credential_subject_user_id) == (expected, expected)
