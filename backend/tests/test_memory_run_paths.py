"""Memory policy plan 15 (PR-A): which memory each execution path reads and writes.

API, channel and subagent runs see no personal memory and get no write tools; a schedule
reads only the owner's agent memory; an approval resume keeps its chain's origin path.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.memory_paths import allowed_scopes_for, memory_access, origin_run_path
from app.agent_runtime.runtime.memory_context import load_memory_context_in, memory_write_policy_in
from app.agent_runtime.runtime_config import AgentConfig
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.memory import UserMemorySettings
from app.schemas.memory import MemoryRecordCreate
from app.services import memory_service
from app.services.memory_service import MemoryScope
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID
from tests.test_memory_service import _seed_agent

USER_FACT = "user-global fact 4411"
AGENT_FACT = "agent-scoped fact 9927"


@pytest.mark.parametrize(
    ("path", "allowed", "expected"),
    [
        ("chat", "both", "both"),
        ("chat", "agent", "agent"),
        ("trigger", "both", "agent"),
        ("trigger", "user", None),
        ("api", "both", None),
        ("channel", "both", None),
        ("subagent", "both", None),
        ("unknown", "both", None),
    ],
)
def test_path_narrows_the_allowed_scopes(path: str, allowed: str, expected: str | None) -> None:
    assert allowed_scopes_for(path, allowed) == expected


@pytest.mark.parametrize(
    ("path", "writes", "mount"),
    [
        ("chat", True, True),
        ("trigger", True, False),
        ("api", False, False),
        ("channel", False, False),
        ("subagent", False, False),
        ("unknown", False, False),
    ],
)
def test_path_write_and_memo_mount(path: str, writes: bool, mount: bool) -> None:
    access = memory_access(path)
    assert (access.write_tools, access.memo_mount) == (writes, mount)


def _cfg(agent_id: uuid.UUID, path: str) -> AgentConfig:
    return AgentConfig(
        org_context=unit_context(TEST_USER_ID),
        provider="openai",
        model_name="gpt-4o",
        api_key=None,
        base_url=None,
        system_prompt="Hi",
        tools_config=[],
        thread_id="thread-a",
        agent_id=str(agent_id),
        user_id=str(TEST_USER_ID),
        run_path=path,
    )


async def _seed_memories(db: AsyncSession) -> uuid.UUID:
    agent_id, _ = await _seed_agent(db)
    context = unit_context(TEST_USER_ID)
    seeds: tuple[tuple[MemoryScope, str], ...] = (("user", USER_FACT), ("agent", AGENT_FACT))
    for scope, text in seeds:
        await memory_service.create_memory_record(
            db,
            context=context,
            payload=MemoryRecordCreate(scope=scope, content=text, agent_id=agent_id),
        )
    return agent_id


@pytest.mark.parametrize(
    ("path", "user_seen", "agent_seen"),
    [
        ("chat", True, True),
        ("trigger", False, True),
        ("api", False, False),
        ("channel", False, False),
        ("subagent", False, False),
    ],
)
@pytest.mark.asyncio
async def test_recalled_memory_follows_the_path(
    db: AsyncSession, path: str, user_seen: bool, agent_seen: bool
) -> None:
    """PR-B2: memories reach a run through automatic recall, never the system prompt."""
    agent_id = await _seed_memories(db)
    conversation_id = await db.scalar(select(Conversation.id))
    assert conversation_id is not None
    run = _run(conversation_id, agent_id, path.upper())
    run.input_preview = "what is the fact 4411 and 9927"
    db.add(run)
    await db.commit()
    cfg = _cfg(agent_id, path)
    cfg.memory_run_id = str(run.id)
    prompt, briefs = await load_memory_context_in(cfg, db)
    block = cfg.memory_recall_block or ""
    assert (USER_FACT in block, AGENT_FACT in block) == (user_seen, agent_seen)
    assert USER_FACT not in prompt and AGENT_FACT not in prompt
    assert len(briefs) == int(user_seen) + int(agent_seen)
    assert ("## 기억 사용 규칙" in prompt) == (user_seen or agent_seen)


@pytest.mark.parametrize(
    ("path", "trigger_mode", "expected"),
    [
        ("chat", False, "auto"),
        ("chat", True, "off"),  # the schedule policy (default off)
        ("trigger", False, "off"),  # a resumed schedule keeps the schedule policy
        ("api", True, "off"),
        ("api", False, "off"),
        ("subagent", False, "off"),
    ],
)
@pytest.mark.asyncio
async def test_write_policy_follows_the_path(
    db: AsyncSession, path: str, trigger_mode: bool, expected: str
) -> None:
    agent_id, _ = await _seed_agent(db)
    db.add(UserMemorySettings(user_id=TEST_USER_ID, memory_write_policy="auto"))
    await db.commit()
    policy = await memory_write_policy_in(_cfg(agent_id, path), db, is_trigger_mode=trigger_mode)
    assert policy == expected


def _run(
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    run_source: str,
    parent: ConversationRun | None = None,
) -> ConversationRun:
    return ConversationRun(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        status="completed",
        source="resume" if parent is not None else "start",
        run_source=run_source,
        parent_run_id=parent.id if parent is not None else None,
    )


@pytest.mark.parametrize("origin", ["TRIGGER", "API", "CHAT"])
@pytest.mark.asyncio
async def test_a_resume_chain_keeps_its_origin_path(db: AsyncSession, origin: str) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    root = _run(conversation_id, agent_id, origin)
    db.add(root)
    await db.flush()
    first = _run(conversation_id, agent_id, "CHAT", root)  # resumes enter as chat
    db.add(first)
    await db.flush()
    second = _run(conversation_id, agent_id, "CHAT", first)
    db.add(second)
    await db.commit()
    assert await origin_run_path(db, second) == origin.lower()


@pytest.mark.asyncio
async def test_a_broken_chain_reads_as_no_personal_memory(db: AsyncSession) -> None:
    agent_id, conversation_id = await _seed_agent(db)
    orphan = _run(conversation_id, agent_id, "CHAT")
    orphan.parent_run_id = uuid.uuid4()  # the parent is gone
    assert await origin_run_path(db, orphan) == "channel"


@pytest.mark.parametrize(("origin", "expected"), [("TRIGGER", "trigger"), (None, "chat")])
@pytest.mark.asyncio
async def test_an_input_respond_resume_runs_on_the_origin_path(
    client, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, origin: str | None, expected: str
) -> None:
    """A schedule resumed from a chat card or the inbox must not regain chat memory."""
    from sqlalchemy import update

    from tests.authz.g03_support import (
        auth_payload,
        commands_url,
        respond_command,
        seed_conversation,
        seed_interrupted,
    )

    conversation = await seed_conversation(db)
    run_id = await seed_interrupted(db, conversation, auth_payload(1))
    await db.execute(
        update(ConversationRun).where(ConversationRun.id == run_id).values(run_source=origin)
    )
    await db.commit()
    started: dict = {}

    async def fake_start(**kwargs: object) -> None:
        started.update(kwargs)

    monkeypatch.setattr(
        "app.routers.conversation_agent_protocol.start_conversation_run", fake_start
    )
    answer = {"decisions": [{"type": "respond", "message": "retry", "attempt": 1}]}
    result = await client.post(commands_url(conversation), json=respond_command(answer))

    assert result.status_code == 200, result.json()
    assert started["cfg"].run_path == expected


@pytest.mark.asyncio
async def test_api_and_trigger_configs_carry_their_path() -> None:
    from types import SimpleNamespace

    from app.agent_runtime.trigger_agent_config import build_trigger_agent_config

    agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        name="A",
        model=SimpleNamespace(
            id=uuid.uuid4(),
            provider="openai",
            model_name="gpt-4o",
            base_url=None,
            context_window=None,
        ),
        model_params=None,
        middleware_configs=None,
        llm_credential=None,
    )
    identity = SimpleNamespace(
        credential_subject_user_id=TEST_USER_ID, identity_mode="fixed", runtime_name="agent_aa"
    )
    source = SimpleNamespace(
        agent=agent,
        identity=identity,
        api_key=None,
        context=unit_context(TEST_USER_ID),
        llm_credential_scope=None,
        effective_prompt="p",
        tools_config=[],
        thread_id="t",
        runtime_policy=AgentConfig.__dataclass_fields__["runtime_policy"].default,
        agent_skills=None,
        fallback_chain=None,
        access_plan=None,
        approval_policy="blocked",
    )
    assert build_trigger_agent_config(source).run_path == "trigger"  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["wait", "stream"])
@pytest.mark.asyncio
async def test_an_agent_api_config_runs_on_the_api_path(
    db: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from tests.authz import prepared_agent_api_testkit as apikit

    apikit.enable(monkeypatch)
    api = await apikit.seed_api_run(db, tmp_path, monkeypatch, mode=mode)
    assert api.execution.config.run_path == "api"
    assert await memory_write_policy_in(api.execution.config, db, is_trigger_mode=False) == "off"


@pytest.mark.parametrize(("origin", "expected"), [("TRIGGER", "trigger"), ("API", "api")])
@pytest.mark.asyncio
async def test_a_rest_resume_runs_on_the_origin_path(
    client, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, origin: str, expected: str
) -> None:
    from sqlalchemy import update

    from app.routers import chat_resume_entries
    from tests.authz.g03_support import seed_conversation, seed_interrupted

    conversation = await seed_conversation(db)
    run_id = await seed_interrupted(
        db, conversation, {"action_requests": [{"name": "send_email", "args": {}}]}
    )
    await db.execute(
        update(ConversationRun).where(ConversationRun.id == run_id).values(run_source=origin)
    )
    await db.commit()
    started: dict = {}

    async def fake_start(**kwargs: object) -> object:
        started.update(kwargs)
        raise RuntimeError("stop after the config")

    monkeypatch.setattr(chat_resume_entries, "start_conversation_run", fake_start)
    with pytest.raises(RuntimeError, match="stop after the config"):
        await client.post(
            f"/api/conversations/{conversation.id}/messages/resume",
            json={"decisions": [{"type": "approve"}]},
        )
    assert started["cfg"].run_path == expected
