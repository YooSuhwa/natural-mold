"""finalize_skill 오케스트레이션 (M5, 스펙 AD-3).

생성/개선/SOURCE_SKILL_CHANGED/secret scan 차단/slug 충돌/바이너리 asset 포함
(Phase 1.5 디스크 zip)/멱등 + 감사 이벤트(confirm_create/apply_improvement/
apply_conflict/secret_scan_blocked/skill_revision.create) + 완료 딥링크 페이로드.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.models.skill_revision import SkillRevision
from app.services import skill_draft_workspace as workspace
from app.services.skill_builder_finalize import finalize_draft_session
from tests.conftest import TEST_USER_ID
from tests.skill_builder_test_helpers import configure_system_llm

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))


def _skill_md(name: str = "notes", body: str = "Use when summarizing meeting notes.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        f"{body}\n"
    )


async def _make_create_session(
    db: AsyncSession, *, skill_md: str | None = None
) -> SkillBuilderSession:
    session = SkillBuilderSession(
        user_id=TEST_USER_ID,
        user_request="회의록 스킬",
        status="active",
    )
    db.add(session)
    await db.flush()
    path = workspace.create_workspace(session.id)
    root = workspace.resolve_workspace_dir(path)
    (root / "SKILL.md").write_text(skill_md or _skill_md(), encoding="utf-8")
    session.draft_workspace_path = path
    await db.commit()
    return session


async def _audit_actions(db: AsyncSession) -> set[str]:
    result = await db.execute(select(AuditEvent.action))
    return {row[0] for row in result.all()}


# ---------------------------------------------------------------------------
# 생성 (create)
# ---------------------------------------------------------------------------


async def test_finalize_create_produces_skill_revision_and_deeplink(
    db: AsyncSession,
) -> None:
    session = await _make_create_session(db)

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert "error_code" not in result, result
    assert result["slug"] == "notes"
    assert result["deeplink"] == f"/skills/{result['skill_id']}/source"
    assert result["validation_result"]["valid"] is True

    skill = await db.get(Skill, uuid.UUID(result["skill_id"]))
    assert skill is not None
    assert skill.kind == "package"
    assert skill.user_id == TEST_USER_ID

    revision = await db.scalar(select(SkillRevision).where(SkillRevision.skill_id == skill.id))
    assert revision is not None
    assert revision.operation == "builder_create"
    assert revision.source_session_id == session.id

    await db.refresh(session)
    assert session.status == "completed"
    assert session.finalized_skill_id == skill.id

    actions = await _audit_actions(db)
    assert "skill_builder.confirm_create" in actions
    assert "skill_revision.create" in actions


async def test_finalize_is_idempotent_after_completion(db: AsyncSession) -> None:
    session = await _make_create_session(db)

    first = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)
    second = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert second["skill_id"] == first["skill_id"]


async def test_finalize_create_resolves_slug_conflict(db: AsyncSession) -> None:
    from app.skills import service as skill_service

    await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Notes",
        slug="notes",
        description="Use when summarizing notes.",
        content=_skill_md(),
    )
    await db.commit()
    session = await _make_create_session(db)

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert "error_code" not in result, result
    assert result["slug"] != "notes"
    assert result["slug"].startswith("notes")


async def test_finalize_blocks_secret_bearing_draft(db: AsyncSession) -> None:
    secret_body = "Use when summarizing meeting notes.\n\nexport AWS_SECRET_ACCESS_KEY=abc123\n"
    session = await _make_create_session(db, skill_md=_skill_md(body=secret_body))

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert result["error_code"] == "VALIDATION_FAILED"
    codes = {issue["code"] for issue in result["validation_result"]["issues"]}
    assert "SECRET_DETECTED" in codes
    assert "skill_builder.secret_scan_blocked" in await _audit_actions(db)
    # 확정 실패 — skills row가 생기지 않는다.
    assert await db.scalar(select(Skill.id)) is None


PNG_BYTES = b"\x89PNG\x00\x00binary"


async def test_finalize_create_includes_binary_asset(db: AsyncSession) -> None:
    """Phase 1.5 — 디스크 기반 zip이 text 어댑터를 우회해 바이너리를 보존한다."""

    from app.storage.paths import resolve_data_path

    session = await _make_create_session(db)
    root = workspace.resolve_workspace_dir(session.draft_workspace_path or "")
    (root / "assets").mkdir()
    (root / "assets" / "logo.png").write_bytes(PNG_BYTES)
    # inputs/(시험 입력)는 패키지 콘텐츠가 아니다 — export 제외 확인용.
    (root / "inputs").mkdir()
    (root / "inputs" / "example.csv").write_text("a,b\n", encoding="utf-8")

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert "error_code" not in result, result
    skill = await db.get(Skill, uuid.UUID(result["skill_id"]))
    assert skill is not None
    stored_root = resolve_data_path(skill.storage_path or "")
    assert (stored_root / "assets" / "logo.png").read_bytes() == PNG_BYTES
    assert not (stored_root / "inputs").exists()


async def test_finalize_blocks_secret_smuggled_in_null_byte_file(db: AsyncSession) -> None:
    """널바이트를 앞에 붙인 파일은 text 어댑터(검증 스캔 소스)에서 빠지지만
    디스크 zip에는 실린다 — 워크스페이스 보조 스캔이 갭을 닫아야 한다
    (Phase 1.5 리뷰)."""

    session = await _make_create_session(db)
    root = workspace.resolve_workspace_dir(session.draft_workspace_path or "")
    (root / "config.txt").write_bytes(b"\x00export AWS_SECRET_ACCESS_KEY=abc123\n")

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert result["error_code"] == "VALIDATION_FAILED"
    codes = {issue["code"] for issue in result["validation_result"]["issues"]}
    assert "SECRET_DETECTED" in codes
    assert "skill_builder.secret_scan_blocked" in await _audit_actions(db)
    assert await db.scalar(select(Skill.id)) is None


async def test_finalize_returns_package_invalid_and_releases_claim(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """zip 추출 가드(크기 상한) 실패는 PACKAGE_INVALID로 사유를 전하고 claim을
    풀어 재시도가 self-heal되어야 한다 (SOURCE_SKILL_NOT_FOUND 경로와 대칭)."""

    session = await _make_create_session(db)
    root = workspace.resolve_workspace_dir(session.draft_workspace_path or "")
    (root / "assets").mkdir()
    (root / "assets" / "big.bin").write_bytes(b"\x00" * 4096)
    monkeypatch.setattr(settings, "skill_max_package_bytes", 1024)

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert result["error_code"] == "PACKAGE_INVALID"
    await db.refresh(session)
    assert session.status == "review"  # CONFIRMING 잠금이 풀려 있어야 한다.

    # 재시도는 CONFIRMING 게이트에 막히지 않는다.
    second = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)
    assert second["error_code"] == "PACKAGE_INVALID"


# ---------------------------------------------------------------------------
# 개선 (improve) — start v2로 시드된 세션 사용
# ---------------------------------------------------------------------------


async def _start_improve_session(
    client: AsyncClient, db: AsyncSession
) -> tuple[SkillBuilderSession, Skill]:
    from app.skills import service as skill_service

    await configure_system_llm(db)
    source = await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Notes",
        slug="notes",
        description="Use when summarizing notes.",
        content=_skill_md(),
    )
    await db.commit()

    start = await client.post(
        BASE,
        json={
            "mode": "improve",
            "source_skill_id": str(source.id),
            "user_request": "더 정확하게",
        },
    )
    assert start.status_code == 201, start.text
    session = await db.get(SkillBuilderSession, uuid.UUID(start.json()["id"]))
    assert session is not None
    return session, source


async def test_finalize_improve_replaces_storage_and_creates_revision(
    client: AsyncClient, db: AsyncSession
) -> None:
    session, source = await _start_improve_session(client, db)
    root = workspace.resolve_workspace_dir(session.draft_workspace_path or "")
    (root / "SKILL.md").write_text(
        _skill_md(body="Use when summarizing meeting notes. Improved."),
        encoding="utf-8",
    )

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert "error_code" not in result, result
    assert result["skill_id"] == str(source.id)
    assert result["mode"] == "improve"

    await db.refresh(source)
    assert source.kind == "package"

    revision = await db.scalar(
        select(SkillRevision)
        .where(SkillRevision.skill_id == source.id)
        .order_by(SkillRevision.revision_number.desc())
    )
    assert revision is not None
    assert revision.operation == "builder_improvement"
    assert "skill_builder.apply_improvement" in await _audit_actions(db)


async def test_finalize_improve_preserves_seeded_binary_asset(
    client: AsyncClient, db: AsyncSession
) -> None:
    """improve 시드 원본의 바이너리 asset이 finalize 후에도 보존된다 (Phase 1.5).

    브리핑 검증 시나리오 — asset(이미지) 있는 package 스킬을 시드한 뒤 텍스트만
    고쳐 확정해도 디스크 기반 zip이 asset을 그대로 싣는다.
    """

    import io
    import zipfile

    from app.skills import service as skill_service
    from app.storage.paths import resolve_data_path

    await configure_system_llm(db)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("notes/SKILL.md", _skill_md())
        zf.writestr("notes/assets/logo.png", PNG_BYTES)
    source = await skill_service.create_package_skill(
        db, user_id=TEST_USER_ID, zip_bytes=buffer.getvalue()
    )
    await db.commit()

    start = await client.post(
        BASE,
        json={
            "mode": "improve",
            "source_skill_id": str(source.id),
            "user_request": "더 정확하게",
        },
    )
    assert start.status_code == 201, start.text
    session = await db.get(SkillBuilderSession, uuid.UUID(start.json()["id"]))
    assert session is not None
    root = workspace.resolve_workspace_dir(session.draft_workspace_path or "")
    assert (root / "assets" / "logo.png").read_bytes() == PNG_BYTES  # 시드 확인
    (root / "SKILL.md").write_text(
        _skill_md(body="Use when summarizing meeting notes. Improved."),
        encoding="utf-8",
    )

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert "error_code" not in result, result
    await db.refresh(source)
    stored_root = resolve_data_path(source.storage_path or "")
    assert (stored_root / "assets" / "logo.png").read_bytes() == PNG_BYTES


async def test_finalize_improve_conflicts_when_source_changed(
    client: AsyncClient, db: AsyncSession
) -> None:
    session, source = await _start_improve_session(client, db)
    # 세션이 열린 사이 원본이 바뀐 상황 재현.
    source.content_hash = "0" * 64
    await db.commit()

    result = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    assert result["error_code"] == "SOURCE_SKILL_CHANGED"
    assert "skill_builder.apply_conflict" in await _audit_actions(db)
    await db.refresh(session)
    assert session.status == "review"
    assert session.finalized_skill_id is None


# ---------------------------------------------------------------------------
# 도구 노출 + HITL 정책 (항상 승인 카드, 세션 동의 불가)
# ---------------------------------------------------------------------------


async def test_finalize_tool_requires_approval_and_never_session_consent() -> None:
    from unittest.mock import MagicMock, patch

    from app.agent_runtime import runtime_component_builder as rcb
    from app.agent_runtime.runtime_config import AgentConfig
    from app.agent_runtime.skill_builder.tools import SESSION_CONSENT_ELIGIBLE_TOOLS

    session_id = uuid.uuid4()
    cfg = AgentConfig(
        provider="openai",
        model_name="gpt-5.4",
        api_key="sk-test",
        base_url=None,
        system_prompt="placeholder",
        tools_config=[],
        thread_id="thread-finalize",
        agent_id="agent-finalize",
        user_id=str(TEST_USER_ID),
        runtime_profile="skill_builder",
        skill_builder_session_id=str(session_id),
        draft_workspace_path=f"skill-drafts/{session_id}",
        # test_skill_draft 동의가 있어도 finalize_skill 카드는 유지되어야 한다.
        skill_builder_consented_tools=["test_skill_draft"],
    )

    with patch.object(rcb, "create_chat_model", return_value=MagicMock()):
        components = await rcb._prepare_runtime_components(
            cfg,
            is_trigger_mode=False,
            include_ask_user=True,
            include_agent_memory_file=True,
        )

    tool_names = {t.name for t in components.tools}
    assert "finalize_skill" in tool_names

    interrupt_on = components.interrupt_on or {}
    assert interrupt_on["finalize_skill"] == {"allowed_decisions": ["approve", "reject"]}
    assert "test_skill_draft" not in interrupt_on
    assert "finalize_skill" not in SESSION_CONSENT_ELIGIBLE_TOOLS


async def test_finalize_releases_claim_on_source_skill_not_found(
    client: AsyncClient, db: AsyncSession
) -> None:
    """R 후속 회귀(재검 발견): claim이 CONFIRMING을 독립 커밋한 뒤
    SOURCE_SKILL_NOT_FOUND로 실패하면 REVIEW로 복귀해야 한다 — 안 그러면
    CONFIRMING 게이트가 재시도를 "다른 finalize 진행 중"이라는 거짓 메시지로
    abandon 지평(14일)까지 영구 차단한다."""

    from app.services.skill_builder_finalize import finalize_draft_session
    from app.skills import service as skill_service

    session, source = await _start_improve_session(client, db)
    assert session.draft_workspace_path is not None

    # 세션이 참조하는 원본 스킬을 삭제해 post-claim 실패를 재현.
    await skill_service.delete_skill(db, source)
    await db.commit()

    first = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)
    assert first["error_code"] == "SOURCE_SKILL_NOT_FOUND"

    await db.refresh(session)
    assert session.status == "review"  # CONFIRMING 잠금이 풀려 있어야 한다.

    # 재시도는 CONFIRMING 게이트에 막히지 않고 같은 오류를 다시 보고한다(self-heal).
    second = await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)
    assert second["error_code"] == "SOURCE_SKILL_NOT_FOUND"


async def test_finalize_releases_claim_when_cancelled(
    client: AsyncClient, db: AsyncSession, monkeypatch
) -> None:
    """3차 리뷰 회귀: 런 취소(CancelledError)는 BaseException이라 도구/서비스의
    Exception 캐치를 모두 통과한다 — claim 해제가 shield로 완료되지 않으면
    CONFIRMING이 abandon 지평까지 잠긴다."""

    import asyncio as _asyncio

    from app.services import skill_builder_service as sbs
    from app.services.skill_builder_finalize import finalize_draft_session

    session, _source = await _start_improve_session(client, db)

    async def cancelled_confirm(*_args, **_kwargs):
        raise _asyncio.CancelledError()

    monkeypatch.setattr(sbs, "confirm_session", cancelled_confirm)

    with pytest.raises(_asyncio.CancelledError):
        await finalize_draft_session(db, session_id=session.id, user_id=TEST_USER_ID)

    await db.refresh(session)
    assert session.status == "review"  # 잠금이 풀려 재시도 가능해야 한다.
