from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.tools.memory import build_memory_tools
from app.exceptions import ValidationError
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.memory import AgentMemorySettings, MemoryRecord, UserMemorySettings
from app.models.model import Model
from app.models.user import User
from app.schemas.memory import MemoryProposalCreate, MemoryRecordCreate, MemoryRecordUpdate
from app.services import memory_service
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_agent(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test User")
    db.add(user)
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=TEST_USER_ID,
        name="Memory Agent",
        system_prompt="Remember useful facts.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()
    conversation = Conversation(agent_id=agent.id, title="Memory Test")
    db.add(conversation)
    await db.commit()
    return agent.id, conversation.id


@pytest.mark.asyncio
async def test_effective_policy_uses_safe_defaults(db: AsyncSession) -> None:
    policy = await memory_service.resolve_effective_policy(db, user_id=TEST_USER_ID)

    assert policy.read_enabled is True
    assert policy.write_policy == "ask"
    assert policy.allowed_scopes == "both"
    assert policy.trigger_write_policy == "off"


@pytest.mark.asyncio
async def test_agent_override_cannot_exceed_user_policy(db: AsyncSession) -> None:
    agent_id, _conversation_id = await _seed_agent(db)
    db.add(
        UserMemorySettings(
            user_id=TEST_USER_ID,
            memory_write_policy="off",
            allowed_scopes="user",
            trigger_memory_write_policy="off",
        )
    )
    db.add(
        AgentMemorySettings(
            agent_id=agent_id,
            memory_policy_override="auto",
            memory_scopes_override="user_and_agent",
            trigger_memory_policy_override="auto",
        )
    )
    await db.commit()

    policy = await memory_service.resolve_effective_policy(
        db,
        user_id=TEST_USER_ID,
        agent_id=agent_id,
    )

    assert policy.write_policy == "off"
    assert policy.allowed_scopes == "user"
    assert policy.trigger_write_policy == "off"


@pytest.mark.asyncio
async def test_agent_only_scope_override_turns_writes_off_when_user_disallows_agent_scope(
    db: AsyncSession,
) -> None:
    agent_id, _conversation_id = await _seed_agent(db)
    db.add(
        UserMemorySettings(
            user_id=TEST_USER_ID,
            memory_write_policy="auto",
            allowed_scopes="user",
        )
    )
    db.add(
        AgentMemorySettings(
            agent_id=agent_id,
            memory_policy_override="inherit",
            memory_scopes_override="agent_only",
        )
    )
    await db.commit()

    policy = await memory_service.resolve_effective_policy(
        db,
        user_id=TEST_USER_ID,
        agent_id=agent_id,
    )

    assert policy.write_policy == "off"
    assert policy.allowed_scopes == "user"
    assert policy.read_enabled is False


@pytest.mark.asyncio
async def test_memory_tool_degrades_auto_save_to_proposal_when_policy_is_ask(
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)

    tools = build_memory_tools(
        user_id=str(TEST_USER_ID),
        agent_id=str(agent_id),
        conversation_id=str(conversation_id),
        is_trigger_mode=False,
        session_factory=TestSession,
    )
    save_user_memory = next(tool for tool in tools if tool.name == "save_user_memory")

    result = await save_user_memory.ainvoke(
        {
            "content": "The user prefers short Korean answers.",
            "reason": "The user explicitly said this preference.",
        }
    )

    payload = json.loads(result)
    assert payload["memory_event"] == "memory_proposed"
    assert payload["scope"] == "user"


@pytest.mark.asyncio
async def test_memory_tool_normalizes_blank_reason_when_policy_is_ask(
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)

    tools = build_memory_tools(
        user_id=str(TEST_USER_ID),
        agent_id=str(agent_id),
        conversation_id=str(conversation_id),
        is_trigger_mode=False,
        session_factory=TestSession,
    )
    save_user_memory = next(tool for tool in tools if tool.name == "save_user_memory")

    result = await save_user_memory.ainvoke(
        {
            "content": "The user prefers Korean answers.",
            "reason": "   ",
        }
    )

    payload = json.loads(result)
    assert payload["memory_event"] == "memory_proposed"
    assert payload["reason"] is None


@pytest.mark.asyncio
async def test_memory_tool_returns_rejection_for_secret_like_content(
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)

    tools = build_memory_tools(
        user_id=str(TEST_USER_ID),
        agent_id=str(agent_id),
        conversation_id=str(conversation_id),
        is_trigger_mode=False,
        session_factory=TestSession,
    )
    save_user_memory = next(tool for tool in tools if tool.name == "save_user_memory")

    result = await save_user_memory.ainvoke(
        {
            "content": "api_key=sk-1234567890abcdef123456",
            "reason": "User asked to remember this credential.",
        }
    )

    payload = json.loads(result)
    assert payload["memory_event"] == "memory_rejected"
    assert payload["reason"] == "MEMORY_SECRET_DETECTED"
    assert "sk-1234567890abcdef123456" not in result
    assert payload["content"] == "<redacted>"


@pytest.mark.asyncio
async def test_get_user_settings_handles_concurrent_first_insert(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_commit = db.commit

    async def _raise_conflict_once() -> None:
        async with TestSession() as other:
            other.add(UserMemorySettings(user_id=TEST_USER_ID))
            await other.commit()
        monkeypatch.setattr(db, "commit", original_commit)
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(db, "commit", _raise_conflict_once)

    settings = await memory_service.get_user_settings(db, TEST_USER_ID)

    assert settings.user_id == TEST_USER_ID


@pytest.mark.asyncio
async def test_get_agent_settings_handles_concurrent_first_insert(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id, _conversation_id = await _seed_agent(db)
    original_commit = db.commit

    async def _raise_conflict_once() -> None:
        async with TestSession() as other:
            other.add(AgentMemorySettings(agent_id=agent_id))
            await other.commit()
        monkeypatch.setattr(db, "commit", original_commit)
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(db, "commit", _raise_conflict_once)

    settings = await memory_service.get_agent_settings(db, agent_id, TEST_USER_ID)

    assert settings is not None
    assert settings.agent_id == agent_id


@pytest.mark.asyncio
async def test_create_memory_record_rejects_secret_like_reason(
    db: AsyncSession,
) -> None:
    with pytest.raises(ValidationError, match="민감정보처럼 보이는 값"):
        await memory_service.create_memory_record(
            db,
            user_id=TEST_USER_ID,
            payload=MemoryRecordCreate(
                scope="user",
                content="The user prefers concise answers.",
                reason="api_key=sk-1234567890abcdef123456",
            ),
        )


@pytest.mark.asyncio
async def test_update_memory_record_rejects_secret_like_reason(
    db: AsyncSession,
) -> None:
    record = await memory_service.create_memory_record(
        db,
        user_id=TEST_USER_ID,
        payload=MemoryRecordCreate(
            scope="user",
            content="The user prefers concise answers.",
            reason="User stated this preference.",
        ),
    )
    assert record is not None

    with pytest.raises(ValidationError, match="민감정보처럼 보이는 값"):
        await memory_service.update_memory_record(
            db,
            memory_id=record.id,
            user_id=TEST_USER_ID,
            payload=MemoryRecordUpdate(reason="password=correct-horse-battery-staple"),
        )


@pytest.mark.asyncio
async def test_create_memory_proposal_rejects_secret_like_reason(
    db: AsyncSession,
) -> None:
    with pytest.raises(ValidationError, match="민감정보처럼 보이는 값"):
        await memory_service.create_memory_proposal(
            db,
            user_id=TEST_USER_ID,
            payload=MemoryProposalCreate(
                scope="user",
                content="The user prefers concise answers.",
                reason="token=abc123456789secret",
            ),
        )


@pytest.mark.asyncio
async def test_approve_memory_proposal_rejects_secret_like_reason(
    db: AsyncSession,
) -> None:
    proposal = await memory_service.create_memory_proposal(
        db,
        user_id=TEST_USER_ID,
        payload=MemoryProposalCreate(
            scope="user",
            content="The user prefers concise answers.",
            reason="User stated this preference.",
        ),
    )
    assert proposal is not None

    with pytest.raises(ValidationError, match="민감정보처럼 보이는 값"):
        await memory_service.approve_memory_proposal(
            db,
            proposal_id=proposal.id,
            user_id=TEST_USER_ID,
            content="The user prefers concise answers.",
            reason="secret=abc123456789secret",
        )


@pytest.mark.asyncio
async def test_memory_tool_saves_immediately_when_policy_is_auto(
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    db.add(UserMemorySettings(user_id=TEST_USER_ID, memory_write_policy="auto"))
    await db.commit()

    tools = build_memory_tools(
        user_id=str(TEST_USER_ID),
        agent_id=str(agent_id),
        conversation_id=str(conversation_id),
        is_trigger_mode=False,
        session_factory=TestSession,
    )
    save_agent_memory = next(tool for tool in tools if tool.name == "save_agent_memory")

    result = await save_agent_memory.ainvoke(
        {
            "content": "This agent should lead research reports with a table.",
            "reason": "The user configured this agent behavior.",
        }
    )

    payload = json.loads(result)
    assert payload["memory_event"] == "memory_saved"
    assert payload["scope"] == "agent"
    assert payload["agent_id"] == str(agent_id)


@pytest.mark.asyncio
async def test_memory_tool_normalizes_blank_reason_when_policy_is_auto(
    db: AsyncSession,
) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    db.add(UserMemorySettings(user_id=TEST_USER_ID, memory_write_policy="auto"))
    await db.commit()

    tools = build_memory_tools(
        user_id=str(TEST_USER_ID),
        agent_id=str(agent_id),
        conversation_id=str(conversation_id),
        is_trigger_mode=False,
        session_factory=TestSession,
    )
    save_agent_memory = next(tool for tool in tools if tool.name == "save_agent_memory")

    result = await save_agent_memory.ainvoke(
        {
            "content": "This agent should lead research reports with a table.",
            "reason": "\t  ",
        }
    )

    payload = json.loads(result)
    assert payload["memory_event"] == "memory_saved"
    assert payload["reason"] is None


def test_render_memory_prompt_caps_runtime_context() -> None:
    records = [
        MemoryRecord(
            user_id=TEST_USER_ID,
            scope="user",
            content=f"memory item {index} " + ("x" * 500),
            reason=None,
            store_path="/memories/user/profile.md",
            status="active",
        )
        for index in range(25)
    ]

    prompt = memory_service.render_memory_prompt(records)

    assert prompt.count("- memory item") <= 20
    assert "memory item 0" not in prompt
    assert "memory item 24" in prompt
    assert len(prompt) <= 12_000


@pytest.mark.asyncio
async def test_list_runtime_memory_records_limits_to_recent_records_in_query(
    db: AsyncSession,
) -> None:
    agent_id, _conversation_id = await _seed_agent(db)
    base_time = datetime(2026, 6, 5, 0, 0, 0)
    for index in range(25):
        timestamp = base_time + timedelta(seconds=index)
        db.add(
            MemoryRecord(
                user_id=TEST_USER_ID,
                scope="user",
                content=f"memory item {index}",
                reason=None,
                store_path="/memories/user/profile.md",
                status="active",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
    await db.commit()

    records = await memory_service.list_runtime_memory_records(
        db,
        user_id=TEST_USER_ID,
        agent_id=agent_id,
        allowed_scopes="user",
    )

    assert len(records) == 20
    assert [record.content for record in records] == [
        f"memory item {index}" for index in range(5, 25)
    ]
