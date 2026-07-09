"""스킬 빌더 챗 런타임 (M3) — 분기/도구/이벤트/redaction 계약.

- ``resolve_agent_context``: runtime_profile 판독 → System LLM 재해석 + 세션
  역참조 + ``moldy.skill_draft`` 페이로드 적재
- ``_prepare_runtime_components``: skill_builder 분기(프롬프트/도구/마운트 교체)
- ``validate_skill``/``generate_evals`` 도구
- ``moldy.skill_draft``(stream-head stable-id) / ``moldy.skill_validation``
  (도구 projection) + 영속 redaction pass-through 등록
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_streaming import stream_agent_response_langgraph
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.skill_builder.eval_schema import parse_evals_json
from app.agent_runtime.skill_builder.tools import build_skill_builder_tools
from app.agent_runtime.skill_validation_projection import (
    skill_validation_event_from_tool_result,
)
from app.config import settings
from app.models.skill_builder_session import SkillBuilderSession
from app.services import skill_draft_workspace as workspace
from tests.agent_runtime.langgraph_streaming_fixtures import ProtocolAgent, sse_payload
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))


_VALID_SKILL_MD = (
    "---\n"
    "name: notes\n"
    'description: "Use when summarizing meeting notes into action items with owners."\n'
    "---\n\n"
    "Use when summarizing meeting notes.\n"
)


async def _make_active_session(db: AsyncSession, *, seed_skill_md: bool = True):
    session = SkillBuilderSession(
        user_id=TEST_USER_ID,
        user_request="회의록 액션 아이템 스킬",
        status="active",
    )
    db.add(session)
    await db.flush()
    path = workspace.create_workspace(session.id)
    if seed_skill_md:
        (workspace.resolve_workspace_dir(path) / "SKILL.md").write_text(
            _VALID_SKILL_MD, encoding="utf-8"
        )
    session.draft_workspace_path = path
    await db.commit()
    return session


# ---------------------------------------------------------------------------
# 런타임 분기 (_prepare_runtime_components)
# ---------------------------------------------------------------------------


async def test_prepare_branch_swaps_prompt_tools_and_mounts_draft() -> None:
    from deepagents.middleware.filesystem import _check_fs_permission

    from app.agent_runtime import runtime_component_builder as rcb

    session_id = uuid.uuid4()
    cfg = AgentConfig(
        provider="openai",
        model_name="gpt-5.4",
        api_key="sk-test",
        base_url=None,
        system_prompt="placeholder — must be replaced",
        tools_config=[],
        thread_id="thread-builder",
        agent_id="agent-builder",
        user_id=str(TEST_USER_ID),
        runtime_profile="skill_builder",
        skill_builder_session_id=str(session_id),
        draft_workspace_path=f"skill-drafts/{session_id}",
    )

    with patch.object(rcb, "create_chat_model", return_value=MagicMock()):
        components = await rcb._prepare_runtime_components(
            cfg,
            is_trigger_mode=False,
            include_ask_user=True,
            include_agent_memory_file=True,
        )

    tool_names = {t.name for t in components.tools}
    assert {"validate_skill", "generate_evals", "ask_user"} <= tool_names
    # 표준 경로 전용 표면은 전부 생략된다.
    assert "execute_in_skill" not in tool_names
    assert "propose_memory" not in tool_names
    assert components.skills_sources is None
    assert components.memory_sources is None

    # 코드 정의 프롬프트로 교체 + 워크스페이스 가상 경로 주입.
    assert "placeholder — must be replaced" not in components.system_prompt
    assert f"/skill-drafts/{session_id}" in components.system_prompt

    # 드래프트 마운트: 자기 세션 write allow, sibling deny.
    assert (
        _check_fs_permission(
            components.permissions, "write", f"/skill-drafts/{session_id}/SKILL.md"
        )
        == "allow"
    )
    assert (
        _check_fs_permission(components.permissions, "read", "/skill-drafts/other/SKILL.md")
        == "deny"
    )


# ---------------------------------------------------------------------------
# resolve_agent_context 분기
# ---------------------------------------------------------------------------


async def test_resolve_agent_context_builder_branch(client, db: AsyncSession) -> None:
    from app.dependencies import CurrentUser
    from app.services.conversation_stream_service import resolve_agent_context
    from tests.skill_builder_test_helpers import configure_system_llm

    await configure_system_llm(db)
    start = await client.post(
        "/api/skill-builder",
        json={"mode": "create", "user_request": "회의록 스킬"},
    )
    assert start.status_code == 201, start.text
    body = start.json()

    user = CurrentUser(
        id=TEST_USER_ID, email="test@test.com", name="Test User", is_super_user=True
    )
    cfg = await resolve_agent_context(db, uuid.UUID(body["conversation_id"]), user)

    assert cfg.runtime_profile == "skill_builder"
    # ADR-019 — 모델은 seed FK가 아니라 System LLM(text_primary) 재해석.
    assert cfg.provider == "openai"
    assert cfg.model_name == "gpt-5.4"
    assert cfg.api_key == "sk-test"
    assert cfg.skill_builder_session_id == body["id"]
    assert cfg.draft_workspace_path == f"skill-drafts/{body['id']}"
    assert cfg.tools_config == []
    assert cfg.subagents_config is None
    brief = cfg.skill_draft_brief
    assert brief is not None
    assert brief["session_id"] == body["id"]
    assert brief["mode"] == "create"


# ---------------------------------------------------------------------------
# 도구: validate_skill / generate_evals
# ---------------------------------------------------------------------------


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


async def test_validate_skill_tool_returns_and_persists_result(db: AsyncSession) -> None:
    session = await _make_active_session(db)
    tools = build_skill_builder_tools(
        session_id=str(session.id),
        workspace_path=session.draft_workspace_path or "",
        session_factory=TestSession,
    )

    output = json.loads(await _tool(tools, "validate_skill").ainvoke({}))

    assert output["session_id"] == str(session.id)
    assert "valid" in output and "issues" in output
    assert isinstance(output.get("compatibility_result"), dict)

    result = await db.execute(
        select(SkillBuilderSession).where(SkillBuilderSession.id == session.id)
    )
    stored = result.scalar_one()
    await db.refresh(stored)
    assert stored.validation_result is not None
    assert stored.validation_result["valid"] == output["valid"]


async def test_generate_evals_writes_schema_valid_file(db: AsyncSession) -> None:
    session = await _make_active_session(db)
    tools = build_skill_builder_tools(
        session_id=str(session.id),
        workspace_path=session.draft_workspace_path or "",
        session_factory=TestSession,
    )

    output = json.loads(
        await _tool(tools, "generate_evals").ainvoke(
            {"intent": "회의록에서 액션 아이템을 표로 추출"}
        )
    )

    assert output["path"] == "evals/evals.json"
    assert output["case_count"] >= 1
    evals_path = (
        workspace.resolve_workspace_dir(session.draft_workspace_path or "")
        / "evals"
        / "evals.json"
    )
    parsed = parse_evals_json(evals_path.read_text(encoding="utf-8"))
    assert len(parsed.evals) == output["case_count"]


async def test_generate_evals_defaults_intent_to_session_request(db: AsyncSession) -> None:
    session = await _make_active_session(db)
    tools = build_skill_builder_tools(
        session_id=str(session.id),
        workspace_path=session.draft_workspace_path or "",
        session_factory=TestSession,
    )

    output = json.loads(await _tool(tools, "generate_evals").ainvoke({}))

    assert output["case_count"] >= 1


# ---------------------------------------------------------------------------
# moldy.skill_draft — stream-head stable-id
# ---------------------------------------------------------------------------


async def test_streaming_emits_skill_draft_at_head_with_stable_id() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "hi"}},
        "seq": 1,
        "event_id": "upstream-1",
    }
    agent = ProtocolAgent([raw_event])
    brief = {
        "session_id": "s1",
        "mode": "create",
        "slug": None,
        "file_count": 1,
        "files": [{"path": "SKILL.md", "size": 120}],
        "changed_count": 1,
    }

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-draft"}},
            run_id="run-draft",
            skill_draft_brief=brief,
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert [payload["method"] for payload in payloads] == [
        "lifecycle",
        "custom",
        "messages",
        "lifecycle",
    ]
    draft_event = payloads[1]["params"]["data"]
    assert draft_event["name"] == "moldy.skill_draft"
    assert draft_event["payload"] == brief
    # replay/reload dedup — stable event id (memory_recalled와 동일 계약).
    assert payloads[1]["event_id"] == "run-draft:skill_draft"


async def test_streaming_omits_skill_draft_when_absent() -> None:
    raw_event = {
        "type": "event",
        "method": "messages",
        "params": {"namespace": [], "data": {"chunk": "hi"}},
        "seq": 1,
        "event_id": "upstream-1",
    }
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-plain"}},
            run_id="run-plain",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    assert all(
        payload["params"]["data"].get("name") != "moldy.skill_draft"
        for payload in payloads
        if payload["method"] == "custom"
    )


# ---------------------------------------------------------------------------
# moldy.skill_validation — 도구 결과 projection
# ---------------------------------------------------------------------------


def _validation_result_json() -> str:
    return json.dumps(
        {
            "session_id": "s1",
            "valid": False,
            "error_count": 1,
            "warning_count": 0,
            "info_count": 0,
            "issues": [
                {
                    "code": "SKILL_MD_MISSING",
                    "severity": "error",
                    "message": "SKILL.md is required.",
                    "path": "SKILL.md",
                }
            ],
            "compatibility_result": {"error_count": 0, "warning_count": 0, "info_count": 0},
        }
    )


async def test_skill_validation_projection_accepts_validate_result() -> None:
    payload = skill_validation_event_from_tool_result(
        "validate_skill", _validation_result_json()
    )
    assert payload is not None
    assert payload["tool_name"] == "validate_skill"
    assert payload["session_id"] == "s1"
    assert payload["validation_result"]["valid"] is False


async def test_skill_validation_projection_rejects_other_tools_and_shapes() -> None:
    assert (
        skill_validation_event_from_tool_result("web_search", _validation_result_json())
        is None
    )
    assert skill_validation_event_from_tool_result("validate_skill", "not json") is None
    assert (
        skill_validation_event_from_tool_result("validate_skill", json.dumps({"ok": 1}))
        is None
    )


async def test_streaming_projects_validate_skill_result_to_custom_event() -> None:
    raw_event = {
        "type": "event",
        "method": "tools",
        "params": {
            "namespace": [],
            "data": {
                "event": "tool-finished",
                "tool_name": "validate_skill",
                "tool_call_id": "call-validate",
                "output": _validation_result_json(),
            },
        },
        "seq": 1,
        "event_id": "tool-finished-1",
    }
    agent = ProtocolAgent([raw_event])

    chunks = [
        chunk
        async for chunk in stream_agent_response_langgraph(
            agent,
            {"messages": []},
            {"configurable": {"thread_id": "thread-validate"}},
            run_id="run-validate",
        )
    ]

    payloads = [sse_payload(chunk) for chunk in chunks]
    custom = [p["params"]["data"] for p in payloads if p["method"] == "custom"]
    assert any(
        event["name"] == "moldy.skill_validation"
        and event["payload"]["validation_result"]["error_count"] == 1
        for event in custom
    )


# ---------------------------------------------------------------------------
# redaction 등록 (영속 경로 pass-through — CLAUDE.md 이름 기반 매처 규칙)
# ---------------------------------------------------------------------------


async def test_persistence_keeps_skill_builder_event_payloads() -> None:
    draft_event = {
        "name": "moldy.skill_draft",
        "payload": {"session_id": "s1", "files": [{"path": "SKILL.md", "size": 10}]},
    }
    validation_event = {
        "name": "moldy.skill_validation",
        "payload": {"validation_result": {"valid": True, "issues": []}},
    }
    assert redact_protocol_data("custom", draft_event) == draft_event
    assert redact_protocol_data("custom", validation_event) == validation_event


# ---------------------------------------------------------------------------
# 첨부 → inputs/ 복사 (run.start 트리거용 DB 헬퍼)
# ---------------------------------------------------------------------------


async def test_copy_conversation_attachments_filters_ownership(
    db: AsyncSession, tmp_path: Path
) -> None:
    from app.models.message_attachment import MessageAttachment

    session = await _make_active_session(db)
    blob = tmp_path / "uploads" / "a.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"csv,data")
    mine = MessageAttachment(
        user_id=TEST_USER_ID,
        filename="mine.csv",
        mime_type="text/csv",
        size_bytes=8,
        storage_path="uploads/a.bin",
        url="/api/uploads/a",
    )
    theirs = MessageAttachment(
        user_id=uuid.uuid4(),
        filename="theirs.csv",
        mime_type="text/csv",
        size_bytes=8,
        storage_path="uploads/a.bin",
        url="/api/uploads/b",
    )
    db.add_all([mine, theirs])
    await db.commit()

    copied = await workspace.copy_conversation_attachments_to_inputs(
        db,
        storage_path=session.draft_workspace_path or "",
        attachment_ids=[mine.id, theirs.id],
        user_id=TEST_USER_ID,
    )

    assert copied == ["inputs/mine.csv"]
