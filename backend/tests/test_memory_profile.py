"""Memory policy plan 15 (PR-B2, 5.12): "내 프로필" and "작업 메모"."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime.memory_context import load_memory_context_in
from app.agent_runtime.runtime_config import AgentConfig
from app.exceptions import ValidationError
from app.models.conversation_run import ConversationRun
from app.models.memory import MemoryRecord
from app.services import memory_lifecycle, memory_profile, memory_writes
from app.services.memory_service import MemoryShape
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID
from tests.test_memory_service import _seed_agent


async def _save(db: AsyncSession, content: str, **kw) -> MemoryRecord:
    result = await memory_writes.write_memory(
        db,
        context=unit_context(TEST_USER_ID),
        write=memory_writes.MemoryWrite(
            scope=kw.pop("scope", "user"),
            content=content,
            agent_id=kw.pop("agent_id", None),
            shape=MemoryShape(),
        ),
    )
    assert result.record is not None
    return result.record


def _cfg(agent_id: uuid.UUID, path: str = "chat") -> AgentConfig:
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        thread_id="t",
        agent_id=str(agent_id),
        user_id=str(TEST_USER_ID),
        run_path=path,
    )


@pytest.mark.asyncio
async def test_profile_lines_reach_the_prompt_by_path(db: AsyncSession) -> None:
    agent_id, _ = await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    await memory_profile.replace_lines(
        db, context, kind="user_profile", agent_id=None, texts=[(None, "존댓말로 답한다")]
    )
    await memory_profile.replace_lines(
        db, context, kind="work_memo", agent_id=agent_id, texts=[(None, "표는 마크다운으로")]
    )
    chat, chat_briefs = await load_memory_context_in(_cfg(agent_id), db)
    assert "존댓말로 답한다" in chat and "표는 마크다운으로" in chat
    assert {b["path"] for b in chat_briefs} == {"profile"}
    # A schedule reads the work memo but never the personal profile (5.0).
    trigger, _ = await load_memory_context_in(_cfg(agent_id, "trigger"), db)
    assert "표는 마크다운으로" in trigger and "존댓말로 답한다" not in trigger
    api, _ = await load_memory_context_in(_cfg(agent_id, "api"), db)
    assert api == ""


@pytest.mark.asyncio
async def test_a_write_over_the_budget_is_refused_and_names_the_longest_line(
    db: AsyncSession,
) -> None:
    await _seed_agent(db)
    texts = [(None, f"규칙 {i} " + "가" * 280) for i in range(3)] + [(None, "짧은 줄")]
    with pytest.raises(ValidationError) as caught:
        await memory_profile.replace_lines(
            db, unit_context(TEST_USER_ID), kind="user_profile", agent_id=None, texts=texts
        )
    assert caught.value.code == "MEMORY_PROFILE_TOO_LONG" and "1번째" in caught.value.message
    view = await memory_profile.view(db, unit_context(TEST_USER_ID), "user_profile", None)
    assert view.lines == ()


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("주민번호 900101-1234568", "MEMORY_PII_RRN"),
        ("가" * 301, "MEMORY_PROFILE_LINE_TOO_LONG"),
        ("   ", "MEMORY_PROFILE_LINE_EMPTY"),
    ],
)
@pytest.mark.asyncio
async def test_a_line_goes_through_the_checks(db: AsyncSession, text: str, code: str) -> None:
    await _seed_agent(db)
    with pytest.raises(ValidationError) as caught:
        await memory_profile.replace_lines(
            db, unit_context(TEST_USER_ID), kind="user_profile", agent_id=None, texts=[(None, text)]
        )
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_a_stale_version_is_refused(db: AsyncSession) -> None:
    await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    first = await memory_profile.replace_lines(
        db, context, kind="user_profile", agent_id=None, texts=[(None, "하나")]
    )
    with pytest.raises(ValidationError) as caught:
        await memory_profile.replace_lines(
            db,
            context,
            kind="user_profile",
            agent_id=None,
            texts=[(None, "둘")],
            expected_version=first.version - 1,
        )
    assert caught.value.code == "MEMORY_PROFILE_CHANGED"


@pytest.mark.asyncio
async def test_a_pinned_memory_is_a_sourced_line_and_leaves_recall(db: AsyncSession) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    record = await _save(db, "회의록은 항상 요약부터 쓴다")
    view = await memory_profile.pin(db, context, memory_id=record.id)
    assert view is not None and [line.sources for line in view.lines] == [(str(record.id),)]
    refreshed = await db.get(MemoryRecord, record.id)
    assert refreshed is not None and refreshed.pinned is True

    run = ConversationRun(
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        status="running",
        source="start",
        run_source="CHAT",
        input_preview="회의록 요약 어떻게 써?",
    )
    db.add(run)
    await db.commit()
    cfg = _cfg(agent_id)
    cfg.memory_run_id = str(run.id)
    prompt, briefs = await load_memory_context_in(cfg, db)
    # In the profile, so not recalled a second time (5.4-3).
    assert "회의록은 항상 요약부터 쓴다" in prompt and not cfg.memory_recall_block
    assert [b["path"] for b in briefs] == ["profile"] and briefs[0]["id"] == str(record.id)

    # Editing the text makes the line the person's own (no source); unpin then has no line.
    edited = await memory_profile.replace_lines(
        db,
        context,
        kind="user_profile",
        agent_id=None,
        texts=[(view.lines[0].id, "회의록은 결론부터 쓴다")],
    )
    assert [(line.origin, line.sources) for line in edited.lines] == [("user", ())]
    refreshed = await db.get(MemoryRecord, record.id)
    assert refreshed is not None and refreshed.pinned is False


@pytest.mark.asyncio
async def test_a_line_whose_source_is_revoked_is_not_read_and_a_delete_removes_it(
    db: AsyncSession,
) -> None:
    agent_id, _ = await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    record = await _save(db, "agent 메모 출처", scope="agent", agent_id=agent_id)
    view = await memory_profile.pin(db, context, memory_id=record.id)
    assert view is not None and view.kind == "work_memo" and view.readable
    await memory_lifecycle.revoke(db, context=context, memory_id=record.id, reason=None)
    revoked = await memory_profile.view(db, context, "work_memo", agent_id)
    assert len(revoked.lines) == 1 and not revoked.readable
    prompt, _ = await load_memory_context_in(_cfg(agent_id), db)
    assert "agent 메모 출처" not in prompt
    await memory_lifecycle.restore(db, context=context, memory_id=record.id)
    await memory_lifecycle.delete_one(db, context=context, memory_id=record.id)
    gone = await memory_profile.view(db, context, "work_memo", agent_id)
    assert gone.lines == ()


@pytest.mark.asyncio
async def test_another_persons_work_memo_stays_theirs(db: AsyncSession) -> None:
    agent_id, _ = await _seed_agent(db)
    await memory_profile.replace_lines(
        db,
        unit_context(TEST_USER_ID),
        kind="work_memo",
        agent_id=agent_id,
        texts=[(None, "내 작업 메모")],
    )
    other = unit_context(uuid.uuid4())
    view = await memory_profile.view(db, other, "work_memo", agent_id)
    assert view.lines == ()
    rows = list(await db.scalars(select(MemoryRecord)))
    assert rows == []
