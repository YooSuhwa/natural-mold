"""F-06: shared agents load and run under the caller's ``agent#can_run`` (11.0, 11.1, D-09).

Legacy keeps the owner-only answer and the same statements; enforce lets a caller with
``can_run`` create a conversation with someone else's agent and prepare its run.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.fake import FakeAuthz
from app.authz.runtime import legacy_service
from app.authz.service import AuthzService
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.agent_subagent import AgentSubAgentLink
from app.models.tool import AgentToolLink
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services import chat_service, conversation_stream_service
from app.services.authorized_runtime import prepare_authorized_runtime
from app.services.chat.trigger_blocked_tools import trigger_blocked_tools_for_agent_tree
from app.services.conversation_runtime_policy import ensure_conversation_runtime_policy
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.f05_support import CALLER, OWNER, identity, loaded, shared_agent
from tests.authz.unit_scope_fixture import unit_context

AS_CALLER = f"user:{CALLER}"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)
    await add_member(db, THIRD_USER_ID)


@contextmanager
def _statements(db: AsyncSession) -> Iterator[list[str]]:
    seen: list[str] = []
    engine = db.bind.sync_engine  # type: ignore[union-attr]

    def record(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_legacy_runs_bind_every_link_with_the_same_statements(db: AsyncSession) -> None:
    agent = await add_agent(db, owner=CALLER)
    for name, status in (("kept", "active"), ("broken", "broken")):
        tool = await add_tool(db, owner=CALLER, name=name)
        db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id, link_status=status))
    await db.commit()
    tree, ctx, run = await loaded(db, agent), unit_context(CALLER), identity(agent)
    with _statements(db) as before:
        old = await chat_service.build_tools_config(tree, context=ctx, db=db, identity=run)
        chat_service.build_agent_skills(tree, ctx)
    with _statements(db) as after:
        new = await prepare_authorized_runtime(
            tree, path="chat", context=ctx, db=db, identity=run, authz=legacy_service()
        )
    assert new.tools_config == old and len(new.plan.tool_links) == 2  # nothing excluded
    assert new.excluded_capabilities == [] and after == before


async def _run_config(db: AsyncSession, agent_id: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    ctx = unit_context(CALLER)
    conversation = await chat_service.create_conversation(db, agent_id, "x", context=ctx)
    await db.commit()
    await ensure_conversation_runtime_policy(db, ConversationPolicyTarget(conversation.id, ctx))
    await db.commit()
    monkeypatch.setattr(
        conversation_stream_service,
        "resolve_llm_api_key_for_agent",
        AsyncMock(return_value="sk-test"),
    )
    user = CurrentUser(id=CALLER, email="caller@test.com", name="Caller")
    return await conversation_stream_service.resolve_agent_context(
        db, conversation.id, user, context=ctx
    )


async def test_a_caller_with_can_run_prepares_a_shared_agent_run(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db, mode="fixed")
    tool = await add_tool(db, owner=OWNER)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    allowed = {
        (AS_CALLER, "can_run", f"agent:{agent.id}"),
        (f"user:{OWNER}", "can_use", f"llm_model:{agent.model_id}"),
    }
    authz = AuthzService(FakeAuthz(frozenset(allowed)), mode="enforce")
    monkeypatch.setattr(runtime, "_current", authz)
    cfg = await _run_config(db, agent.id, monkeypatch)
    assert cfg.agent_id == str(agent.id)
    assert [entry["tool_id"] for entry in cfg.tools_config] == [str(tool.id)]
    assert cfg.access_plan is not None and cfg.access_plan.subject.principal.kind == "agent"
    assert cfg.credential_subject_user_id == str(OWNER)


async def test_a_shared_agent_stays_hidden_without_can_run_or_in_legacy(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = (await shared_agent(db, mode="fixed")).id
    for service in (legacy_service(), AuthzService(FakeAuthz(), mode="enforce")):
        monkeypatch.setattr(runtime, "_current", service)
        with pytest.raises(AppError) as caught:
            await _run_config(db, agent_id, monkeypatch)
        assert caught.value.status == 404
        await db.rollback()


async def test_child_links_are_decided_for_the_parents_subject(db: AsyncSession) -> None:
    parent = await shared_agent(db, mode="fixed")
    lent = await add_agent(db, owner=THIRD_USER_ID)
    kept_out = await add_agent(db, owner=THIRD_USER_ID)
    for position, child in enumerate((lent, kept_out)):
        db.add(
            AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=child.id, position=position)
        )
    await db.commit()
    allowed = {
        (AS_CALLER, "can_run", f"agent:{parent.id}"),
        (f"user:{OWNER}", "can_use", f"llm_model:{parent.model_id}"),
        (f"agent:{parent.id}", "can_run", f"agent:{lent.id}"),
    }
    authz = AuthzService(FakeAuthz(frozenset(allowed)), mode="enforce")
    runtime_ = await prepare_authorized_runtime(
        await loaded(db, parent),
        path="chat",
        context=unit_context(CALLER),
        db=db,
        identity=identity(parent),
        authz=authz,
    )
    assert [link.sub_agent_id for link in runtime_.sub_agent_links] == [lent.id]
    assert [(e.kind, e.resource_id, e.reason) for e in runtime_.plan.excluded] == [
        ("agent", kept_out.id, "denied")
    ]


async def test_the_runtime_policy_read_rechecks_can_run(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L5: my conversation's shared agent is read for its policy only while I may run it."""
    agent = await shared_agent(db, mode="fixed")
    ctx = unit_context(CALLER)
    allowed = {(AS_CALLER, "can_run", f"agent:{agent.id}")}
    monkeypatch.setattr(
        runtime, "_current", AuthzService(FakeAuthz(frozenset(allowed)), mode="enforce")
    )
    conversation = await chat_service.create_conversation(db, agent.id, "x", context=ctx)
    await db.commit()
    monkeypatch.setattr(runtime, "_current", AuthzService(FakeAuthz(), mode="enforce"))
    with pytest.raises(AppError) as caught:
        await ensure_conversation_runtime_policy(db, ConversationPolicyTarget(conversation.id, ctx))
    assert caught.value.status == 404


async def test_trigger_probes_follow_the_plan(db: AsyncSession) -> None:
    """L1: a denied optional tool is left out of the trigger probe instead of failing it."""
    agent = await add_agent(db, owner=OWNER)
    tool = await add_tool(db, owner=THIRD_USER_ID)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    tree, ctx = await loaded(db, agent), unit_context(OWNER)
    allowed = {(f"user:{OWNER}", "can_use", f"llm_model:{agent.model_id}")}
    authz = AuthzService(FakeAuthz(frozenset(allowed)), mode="enforce")
    runtime_ = await prepare_authorized_runtime(
        tree, path="trigger", context=ctx, db=db, identity=None, authz=authz
    )
    assert runtime_.tools_config == [] and len(runtime_.plan.excluded) == 1
    probe = await trigger_blocked_tools_for_agent_tree(tree, db=db, context=ctx, plan=runtime_.plan)
    assert probe == []
    with pytest.raises(AppError):  # without the plan: the legacy owner check
        await trigger_blocked_tools_for_agent_tree(tree, db=db, context=ctx)
