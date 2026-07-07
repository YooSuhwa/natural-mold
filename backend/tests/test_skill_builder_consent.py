"""HITL 세션 동의 + test_skill_draft 샌드박스 (M4, 스펙 AD-3/AD-4).

- ``scope:"session"`` 동의: 기록 + 표준 approve 변환(비표준 키가 미들웨어에
  도달하지 않음), finalize_skill 불가, requires_network 드래프트 불가
- 동의 후 resolve → interrupt 정책에서 제외 (1회차 카드 → 동의 → 2회차 무카드)
- ``test_skill_draft``: fabricated descriptor로 기존 샌드박스 정책 전체 상속
- 인터럽트 wire의 ``session_consent_eligible`` 플래그 주석
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.skill_builder.tools import build_skill_builder_tools
from app.config import settings
from app.models.skill_builder_session import SkillBuilderSession
from app.routers.conversation_agent_protocol_consent import (
    apply_session_consent_decisions,
)
from app.services import skill_draft_workspace as workspace
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

_NETWORK_MOLDY_YAML = "execution_profile:\n  requires_network: true\n"


async def _make_session(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID | None = None,
    requires_network: bool = False,
) -> SkillBuilderSession:
    session = SkillBuilderSession(
        user_id=TEST_USER_ID,
        user_request="회의록 스킬",
        status="active",
        conversation_id=conversation_id,
    )
    db.add(session)
    await db.flush()
    path = workspace.create_workspace(session.id)
    root = workspace.resolve_workspace_dir(path)
    (root / "SKILL.md").write_text(_VALID_SKILL_MD, encoding="utf-8")
    if requires_network:
        (root / "agents").mkdir(exist_ok=True)
        (root / "agents" / "moldy.yaml").write_text(_NETWORK_MOLDY_YAML, encoding="utf-8")
    session.draft_workspace_path = path
    await db.commit()
    return session


def _resume(decisions: list[dict[str, object]]):
    from app.routers.conversation_agent_protocol_resume import (
        ResumePayload,
        SubmittedInterruptResponse,
    )

    response = {"decisions": decisions}
    return ResumePayload(
        input_payload={"int-1": response},
        interrupt_id="int-1",
        submitted=(
            SubmittedInterruptResponse(interrupt_id="int-1", namespace=(), response=response),
        ),
    )


def _pending(action_names: list[str]):
    return [
        {
            "id": "int-1",
            "ns": [],
            "value": {
                "action_requests": [
                    {"name": name, "args": {"command": "python scripts/x.py"}}
                    for name in action_names
                ],
                "review_configs": [
                    {"action_name": name, "allowed_decisions": ["approve", "reject"]}
                    for name in action_names
                ],
            },
        }
    ]


# ---------------------------------------------------------------------------
# 동의 기록 + scope 제거
# ---------------------------------------------------------------------------


async def test_consent_recorded_and_scope_stripped(db: AsyncSession) -> None:
    conversation_id = uuid.uuid4()
    session = await _make_session(db, conversation_id=conversation_id)
    decision: dict[str, object] = {"type": "approve", "scope": "session"}
    resume = _resume([decision])

    recorded = await apply_session_consent_decisions(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        resume=resume,
        pending_interrupts=_pending(["test_skill_draft"]),
    )

    assert recorded == ["test_skill_draft"]
    # 비표준 키는 미들웨어로 내려가지 않는다 — decision dict에서 제거됨.
    assert "scope" not in decision
    assert decision == {"type": "approve"}

    await db.refresh(session)
    assert session.tool_consents is not None
    assert session.tool_consents["test_skill_draft"]["scope"] == "session"


async def test_consent_not_recorded_for_finalize_skill(db: AsyncSession) -> None:
    """finalize_skill은 항상 승인 카드 — scope는 벗기되 기록하지 않는다."""

    conversation_id = uuid.uuid4()
    session = await _make_session(db, conversation_id=conversation_id)
    decision: dict[str, object] = {"type": "approve", "scope": "session"}

    recorded = await apply_session_consent_decisions(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        resume=_resume([decision]),
        pending_interrupts=_pending(["finalize_skill"]),
    )

    assert recorded == []
    assert "scope" not in decision
    await db.refresh(session)
    assert not session.tool_consents


async def test_consent_not_recorded_for_network_draft(db: AsyncSession) -> None:
    """requires_network 드래프트는 세션 동의 불가 (AD-4 경계) — 매번 카드."""

    conversation_id = uuid.uuid4()
    session = await _make_session(db, conversation_id=conversation_id, requires_network=True)
    decision: dict[str, object] = {"type": "approve", "scope": "session"}

    recorded = await apply_session_consent_decisions(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        resume=_resume([decision]),
        pending_interrupts=_pending(["test_skill_draft"]),
    )

    assert recorded == []
    assert "scope" not in decision
    await db.refresh(session)
    assert not session.tool_consents


async def test_consent_ignores_reject_decisions(db: AsyncSession) -> None:
    conversation_id = uuid.uuid4()
    await _make_session(db, conversation_id=conversation_id)
    decision: dict[str, object] = {"type": "reject", "scope": "session"}

    recorded = await apply_session_consent_decisions(
        db,
        conversation_id=conversation_id,
        user_id=TEST_USER_ID,
        resume=_resume([decision]),
        pending_interrupts=_pending(["test_skill_draft"]),
    )

    assert recorded == []
    assert "scope" not in decision


# ---------------------------------------------------------------------------
# 동의 → 정책 제외 (1회차 카드 → 2회차 무카드)
# ---------------------------------------------------------------------------


async def _builder_cfg(session_id: uuid.UUID, **overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "provider": "openai",
        "model_name": "gpt-5.4",
        "api_key": "sk-test",
        "base_url": None,
        "system_prompt": "placeholder",
        "tools_config": [],
        "thread_id": "thread-consent",
        "agent_id": "agent-consent",
        "user_id": str(TEST_USER_ID),
        "runtime_profile": "skill_builder",
        "skill_builder_session_id": str(session_id),
        "draft_workspace_path": f"skill-drafts/{session_id}",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


async def test_interrupt_policy_includes_sandbox_until_consented() -> None:
    from app.agent_runtime import runtime_component_builder as rcb

    session_id = uuid.uuid4()
    cfg = await _builder_cfg(session_id)

    with patch.object(rcb, "create_chat_model", return_value=MagicMock()):
        components = await rcb._prepare_runtime_components(
            cfg,
            is_trigger_mode=False,
            include_ask_user=True,
            include_agent_memory_file=True,
        )

    interrupt_on = components.interrupt_on or {}
    # 1회차: CODE_EXECUTION 위험 메타에서 승인 카드 정책 생성 (approve/reject).
    assert interrupt_on["test_skill_draft"] == {"allowed_decisions": ["approve", "reject"]}
    # AD-3 과승인 방지 — 드래프트 파일 편집은 승인 카드 없이 진행.
    assert "write_file" not in interrupt_on
    assert "edit_file" not in interrupt_on


async def test_interrupt_policy_excludes_consented_sandbox() -> None:
    from app.agent_runtime import runtime_component_builder as rcb

    session_id = uuid.uuid4()
    cfg = await _builder_cfg(session_id, skill_builder_consented_tools=["test_skill_draft"])

    with patch.object(rcb, "create_chat_model", return_value=MagicMock()):
        components = await rcb._prepare_runtime_components(
            cfg,
            is_trigger_mode=False,
            include_ask_user=True,
            include_agent_memory_file=True,
        )

    interrupt_on = components.interrupt_on or {}
    # 2회차(동의 후): 무카드.
    assert "test_skill_draft" not in interrupt_on
    assert "ask_user" in interrupt_on  # ask_user 인터럽트는 유지


async def test_resolve_threads_consent_and_offer(client, db: AsyncSession) -> None:
    from app.dependencies import CurrentUser
    from app.services import skill_builder_service
    from app.services.conversation_stream_service import resolve_agent_context
    from tests.skill_builder_test_helpers import configure_system_llm

    await configure_system_llm(db)
    start = await client.post(
        "/api/skill-builder", json={"mode": "create", "user_request": "회의록"}
    )
    body = start.json()
    user = CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test User", is_super_user=True)
    conversation_id = uuid.UUID(body["conversation_id"])

    before = await resolve_agent_context(db, conversation_id, user)
    assert before.skill_builder_consented_tools is None
    assert before.skill_builder_consent_offer_tools == ["test_skill_draft"]

    session = await skill_builder_service.get_session(db, uuid.UUID(body["id"]), TEST_USER_ID)
    assert session is not None
    await skill_builder_service.record_tool_consents(db, session, tool_names=["test_skill_draft"])
    await db.commit()

    after = await resolve_agent_context(db, conversation_id, user)
    assert after.skill_builder_consented_tools == ["test_skill_draft"]
    assert after.skill_builder_consent_offer_tools is None


# ---------------------------------------------------------------------------
# 인터럽트 wire 주석 (session_consent_eligible)
# ---------------------------------------------------------------------------


async def test_annotate_session_consent_eligibility_marks_matching_configs() -> None:
    from app.agent_runtime.langgraph_streaming import (
        _annotate_session_consent_eligibility,
    )

    event = {
        "data": {
            "interrupt_id": "int-1",
            "payload": {
                "action_requests": [
                    {"name": "test_skill_draft", "args": {"command": "python x.py"}}
                ],
                "review_configs": [
                    {"action_name": "test_skill_draft", "allowed_decisions": ["approve", "reject"]},
                    {"action_name": "finalize_skill", "allowed_decisions": ["approve", "reject"]},
                ],
            },
        }
    }

    _annotate_session_consent_eligibility(event, ["test_skill_draft"])  # type: ignore[arg-type]

    configs = event["data"]["payload"]["review_configs"]
    assert configs[0]["session_consent_eligible"] is True
    assert "session_consent_eligible" not in configs[1]


# ---------------------------------------------------------------------------
# test_skill_draft — fabricated descriptor + 샌드박스 상속
# ---------------------------------------------------------------------------


def _sandbox_tools(session: SkillBuilderSession):
    return build_skill_builder_tools(
        session_id=str(session.id),
        workspace_path=session.draft_workspace_path or "",
        session_factory=TestSession,
        user_id=str(TEST_USER_ID),
        agent_id=None,
        credential_subject_user_id=str(TEST_USER_ID),
        include_runtime_tools=True,
    )


async def test_test_skill_draft_runs_draft_script_in_sandbox(
    db: AsyncSession, tmp_path: Path
) -> None:
    session = await _make_session(db)
    root = workspace.resolve_workspace_dir(session.draft_workspace_path or "")
    (root / "scripts").mkdir()
    (root / "scripts" / "hello.py").write_text("print('HELLO_FROM_DRAFT')\n", encoding="utf-8")

    tools = _sandbox_tools(session)
    tool = next(t for t in tools if t.name == "test_skill_draft")
    result = await tool.ainvoke({"command": "python scripts/hello.py"})

    assert "HELLO_FROM_DRAFT" in result
    # 저장 전 시험 — 원본 워크스페이스는 그대로, 마운트 복사본으로 실행됐다.
    mounted = tmp_path / "runtime" / f"skill-draft-{session.id}" / "skills" / "notes"
    assert (mounted / "scripts" / "hello.py").is_file()


async def test_test_skill_draft_inherits_interpreter_allowlist(
    db: AsyncSession,
) -> None:
    """기존 subprocess 정책 무변경 상속 (§6-6) — 비허용 인터프리터 거부."""

    session = await _make_session(db)
    tools = _sandbox_tools(session)
    tool = next(t for t in tools if t.name == "test_skill_draft")

    result = await tool.ainvoke({"command": "rm -rf /"})

    assert result.startswith("Error")


async def test_test_skill_draft_carries_code_execution_risk(db: AsyncSession) -> None:
    from app.tools.risk import get_tool_risk

    session = await _make_session(db)
    tools = _sandbox_tools(session)
    tool = next(t for t in tools if t.name == "test_skill_draft")

    risk = get_tool_risk(tool)
    assert risk.risk_level.value == "code_execution"
    assert list(risk.allowed_decisions) == ["approve", "reject"]
    assert risk.trigger_safe is False
