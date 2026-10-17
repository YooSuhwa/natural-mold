"""F-09: memory on shared agents (plan 11.8; done-when "비편집자 저장은 제안으로").

* user x agent memory stays the caller's own: a ``can_run`` runner of someone else's
  agent reads/writes only their records; the owner never sees them (and vice versa);
* a non-editor's shared-memo write is a proposal (``scope='agent_memo'``) that only the
  agent's editors decide; the private proposal routes never expose or decide it;
* legacy/shadow and enforce without ``can_run`` keep the owner-only answers.
"""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import runtime_config
from app.authz import runtime
from app.authz.runtime import legacy_service
from app.exceptions import AppError
from app.models.memory import AgentMemorySettings, MemoryRecord
from app.schemas.agent_memo import MAX_AGENT_MEMO_LENGTH
from app.schemas.memory import MemoryRecordCreate
from app.services import agent_memo_service, chat_service, memory_service
from tests.authz.e01_support import audit_actions, seed
from tests.authz.f05_support import CALLER, OWNER, enforce, shared_agent
from tests.authz.unit_scope_fixture import unit_context

AS_CALLER = f"user:{CALLER}"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await seed(db)
    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path)


def _can_run(monkeypatch: pytest.MonkeyPatch, agent_id: object) -> None:
    monkeypatch.setattr(runtime, "_current", enforce((AS_CALLER, "can_run", f"agent:{agent_id}")))


async def _save(
    db: AsyncSession, user: UUID, agent_id: UUID, text: str, conv: UUID | None = None
) -> MemoryRecord | None:
    payload = MemoryRecordCreate(
        scope="agent", content=text, agent_id=agent_id, source_conversation_id=conv
    )
    return await memory_service.create_memory_record(
        db, context=unit_context(user), payload=payload
    )


@pytest.mark.parametrize("service", ["legacy", "enforce_without_can_run"])
async def test_a_shared_agent_keeps_memory_owner_only(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, service: str
) -> None:
    agent = await shared_agent(db)
    current = legacy_service() if service == "legacy" else enforce()
    monkeypatch.setattr(runtime, "_current", current)
    with pytest.raises(AppError) as caught:
        await memory_service.resolve_effective_policy(
            db, context=unit_context(CALLER), agent_id=agent.id
        )
    assert caught.value.status == 404
    assert await _save(db, CALLER, agent.id, "nope") is None
    assert await _propose(db, agent.id) is None


async def test_a_runner_keeps_private_memory_on_a_shared_agent(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db)
    db.add(AgentMemorySettings(agent_id=agent.id, memory_policy_override="ask"))
    await db.commit()
    _can_run(monkeypatch, agent.id)
    caller = unit_context(CALLER)
    conversation = await chat_service.create_conversation(db, agent.id, "x", context=caller)
    await db.commit()
    policy = await memory_service.resolve_effective_policy(db, context=caller, agent_id=agent.id)
    assert policy.write_policy == "ask"  # the agent's own settings apply to every runner
    mine = await _save(db, CALLER, agent.id, "caller likes tea", conversation.id)
    theirs = await _save(db, OWNER, agent.id, "owner likes coffee")
    assert mine is not None and mine.user_id == CALLER and theirs is not None

    async def runtime_records(user: UUID) -> list[str]:
        rows = await memory_service.list_runtime_memory_records(
            db, context=unit_context(user), agent_id=agent.id, allowed_scopes="both"
        )
        return [row.content for row in rows]

    assert await runtime_records(CALLER) == ["caller likes tea"]
    assert await runtime_records(OWNER) == ["owner likes coffee"]
    listed = await memory_service.list_memory_records(db, context=caller, agent_id=agent.id)
    assert [row.id for row in listed] == [mine.id]
    assert await memory_service.get_memory_record(db, memory_id=theirs.id, context=caller) is None
    assert await memory_service.delete_memory_record(db, memory_id=mine.id, context=caller)


async def _enable(db: AsyncSession, agent_id: UUID) -> None:
    db.add(AgentMemorySettings(agent_id=agent_id, shared_memo_enabled=True))
    await db.commit()


async def _propose(db: AsyncSession, agent_id: UUID, content: str = "Reports go to #ops"):
    return await memory_service.create_agent_memo_proposal(
        db,
        context=unit_context(CALLER),
        agent_id=agent_id,
        content=content,
        reason="team rule",
        conversation_id=None,
    )


async def test_a_non_editor_memo_write_becomes_a_proposal_editors_decide(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db)
    _can_run(monkeypatch, agent.id)
    caller = unit_context(CALLER)
    with pytest.raises(AppError) as off:  # new agents start with the memo switched off
        await _propose(db, agent.id)
    assert off.value.code == "AGENT_MEMO_DISABLED"
    await _enable(db, agent.id)
    proposal = await _propose(db, agent.id)
    assert proposal is not None and proposal.scope == "agent_memo"
    assert proposal.user_id == CALLER and proposal.status == "pending"
    # the private proposal surface neither shows nor decides it
    hidden = await memory_service.get_memory_proposal(db, proposal_id=proposal.id, context=caller)
    assert hidden is None
    assert (
        await memory_service.approve_memory_proposal(db, proposal_id=proposal.id, context=caller)
        is None
    )
    assert agent_memo_service.read_memo(agent.id) == ""  # nothing was written
    editor = unit_context(CALLER)  # the route decided can_edit; the service trusts it
    assert [p.id for p in await agent_memo_service.list_memo_proposals(db, agent)] == [proposal.id]
    decided = await agent_memo_service.decide_memo_proposal(
        db, actor=editor, agent=agent, proposal_id=proposal.id, approve=True
    )
    assert decided is not None and decided.status == "approved"
    assert agent_memo_service.read_memo(agent.id) == "- Reports go to #ops\n"
    again = await agent_memo_service.decide_memo_proposal(
        db, actor=editor, agent=agent, proposal_id=proposal.id, approve=True
    )
    assert again is None  # idempotent: decided once, appended once
    assert agent_memo_service.read_memo(agent.id) == "- Reports go to #ops\n"
    assert await audit_actions(agent.id) == ["agent_memo.proposal_decide"]


async def test_pending_proposals_are_capped_and_approval_respects_the_size_cap(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db)
    _can_run(monkeypatch, agent.id)
    await _enable(db, agent.id)
    for index in range(agent_memo_service.MAX_PENDING_PER_USER):
        await _propose(db, agent.id, f"note {index}")
    with pytest.raises(AppError) as capped:
        await _propose(db, agent.id, "one too many")
    assert capped.value.code == "AGENT_MEMO_PROPOSAL_LIMIT"
    path = agent_memo_service.memo_path(agent.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (MAX_AGENT_MEMO_LENGTH - 4))
    agent_id, first_id = agent.id, (await agent_memo_service.list_memo_proposals(db, agent))[0].id
    with pytest.raises(AppError) as big:
        await agent_memo_service.decide_memo_proposal(
            db, actor=unit_context(OWNER), agent=agent, proposal_id=first_id, approve=True
        )
    assert big.value.code == "AGENT_MEMO_TOO_LARGE"
    assert len(agent_memo_service.read_memo(agent_id)) == MAX_AGENT_MEMO_LENGTH - 4
    await db.refresh(agent)
    still = await agent_memo_service.list_memo_proposals(db, agent)
    assert first_id in [row.id for row in still]  # nothing committed


async def test_a_failed_commit_leaves_the_memo_untouched(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db)
    await agent_memo_service.update_memo(
        db, actor=unit_context(OWNER), agent=agent, content="# Notes\n"
    )

    async def broken() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", broken)
    with pytest.raises(RuntimeError):
        await agent_memo_service.update_memo(
            db, actor=unit_context(OWNER), agent=agent, content="# Lost\n"
        )
    assert agent_memo_service.read_memo(agent.id) == "# Notes\n"
    assert not list(agent_memo_service.memo_path(agent.id).parent.glob("*.tmp"))


async def test_memo_update_replaces_the_file_and_audits_without_text(
    db: AsyncSession,
) -> None:
    agent = await shared_agent(db)
    text, enabled = await agent_memo_service.update_memo(
        db, actor=unit_context(OWNER), agent=agent, content="# Notes\n", enabled=True
    )
    assert (text, enabled) == ("# Notes\n", True)
    assert agent_memo_service.read_memo(agent.id) == "# Notes\n"
    with pytest.raises(AppError):
        await agent_memo_service.update_memo(
            db, actor=unit_context(OWNER), agent=agent, content="api_key = sk-abcdefghijklmnopqrst"
        )
    assert agent_memo_service.read_memo(agent.id) == "# Notes\n"
    assert await audit_actions(agent.id) == ["agent_memo.update"]
