"""skill_draft_workspace 서비스 계약 (스펙 AD-2).

생성/시드(improve 복사)/첨부→inputs 복사/디렉토리→SkillDraftFile 어댑터/
GC(세션 상태 기준 — active 보존)를 검증한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill_builder_session import SkillBuilderSession
from app.services import skill_draft_workspace as workspace
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _make_session(
    db: AsyncSession,
    *,
    status: str,
    age_hours: int,
    with_workspace: bool = True,
    with_conversation: bool = False,
) -> SkillBuilderSession:
    session = SkillBuilderSession(
        user_id=TEST_USER_ID,
        user_request="test",
        status=status,
        # 실제 v2 플로우는 start에서 대화를 attach한다 — conversation 없는 오래된
        # 세션은 dead로 간주되어 abandoned 전이 대상 (R2). aiosqlite는 FK 미강제.
        conversation_id=uuid.uuid4() if with_conversation else None,
    )
    db.add(session)
    await db.flush()
    if with_workspace:
        path = workspace.create_workspace(session.id)
        (workspace.resolve_workspace_dir(path) / "SKILL.md").write_text("# draft\n")
        session.draft_workspace_path = path
    # onupdate가 updated_at을 now로 되돌리므로, 마지막 flush에서 명시 설정.
    session.updated_at = _now() - timedelta(hours=age_hours)
    await db.commit()
    return session


# ---------------------------------------------------------------------------
# 생성 / 시드
# ---------------------------------------------------------------------------


async def test_create_workspace_is_idempotent_and_relative(tmp_path: Path) -> None:
    session_id = uuid.uuid4()

    first = workspace.create_workspace(session_id)
    second = workspace.create_workspace(session_id)

    assert first == second == f"skill-drafts/{session_id}"
    assert not Path(first).is_absolute()
    assert (tmp_path / "skill-drafts" / str(session_id)).is_dir()


async def test_seed_from_text_skill_creates_skill_md(tmp_path: Path) -> None:
    src = tmp_path / "skills" / "notes"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("# original\n")
    # text-kind: storage_path가 단일 파일을 가리키는 형태.
    skill = SimpleNamespace(slug="notes", storage_path="skills/notes/SKILL.md")
    session_id = uuid.uuid4()

    path = workspace.seed_workspace_from_skill(skill, session_id)  # type: ignore[arg-type]

    seeded = workspace.resolve_workspace_dir(path)
    assert (seeded / "SKILL.md").read_text() == "# original\n"


async def test_seed_from_package_skill_copies_tree_and_wipes_existing(
    tmp_path: Path,
) -> None:
    src = tmp_path / "skills" / "pack"
    (src / "references").mkdir(parents=True)
    (src / "SKILL.md").write_text("# pack\n")
    (src / "references" / "guide.md").write_text("guide\n")
    skill = SimpleNamespace(slug="pack", storage_path="skills/pack")
    session_id = uuid.uuid4()

    # 기존 워크스페이스 내용은 시드 시 wipe (멱등 재시드).
    stale = workspace.create_workspace(session_id)
    (workspace.resolve_workspace_dir(stale) / "stale.txt").write_text("stale")

    path = workspace.seed_workspace_from_skill(skill, session_id)  # type: ignore[arg-type]

    seeded = workspace.resolve_workspace_dir(path)
    assert (seeded / "SKILL.md").read_text() == "# pack\n"
    assert (seeded / "references" / "guide.md").read_text() == "guide\n"
    assert not (seeded / "stale.txt").exists()

    # 드래프트 편집이 원본으로 역류하지 않는다 (복사 — symlink 금지).
    (seeded / "SKILL.md").write_text("# edited\n")
    assert (src / "SKILL.md").read_text() == "# pack\n"


async def test_seed_with_missing_source_yields_empty_workspace(
    tmp_path: Path,
) -> None:
    skill = SimpleNamespace(slug="ghost", storage_path="skills/ghost")
    session_id = uuid.uuid4()

    path = workspace.seed_workspace_from_skill(skill, session_id)  # type: ignore[arg-type]

    seeded = workspace.resolve_workspace_dir(path)
    assert seeded.is_dir()
    assert list(seeded.iterdir()) == []


# ---------------------------------------------------------------------------
# 첨부 → inputs/ 복사
# ---------------------------------------------------------------------------


def _attachment(tmp_path: Path, *, filename: str, body: bytes = b"data") -> SimpleNamespace:
    blob = tmp_path / "uploads" / f"{uuid.uuid4()}.bin"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(body)
    return SimpleNamespace(
        id=uuid.uuid4(),
        filename=filename,
        storage_path=f"uploads/{blob.name}",
    )


async def test_copy_attachments_sanitizes_and_deduplicates(tmp_path: Path) -> None:
    session_id = uuid.uuid4()
    path = workspace.create_workspace(session_id)
    attachments = [
        _attachment(tmp_path, filename="example.csv", body=b"a,b\n"),
        # 경로 성분은 제거되어야 한다 (traversal 차단).
        _attachment(tmp_path, filename="../../evil.txt", body=b"evil"),
        # 동일 이름 충돌 → 순번 부여.
        _attachment(tmp_path, filename="example.csv", body=b"c,d\n"),
    ]

    copied = workspace.copy_attachments_to_inputs(path, attachments)  # type: ignore[arg-type]

    assert copied == ["inputs/example.csv", "inputs/evil.txt", "inputs/example-1.csv"]
    inputs = workspace.resolve_workspace_dir(path) / "inputs"
    assert (inputs / "example.csv").read_bytes() == b"a,b\n"
    assert (inputs / "example-1.csv").read_bytes() == b"c,d\n"
    assert not (tmp_path / "evil.txt").exists()


async def test_copy_attachments_skips_missing_blob(tmp_path: Path) -> None:
    session_id = uuid.uuid4()
    path = workspace.create_workspace(session_id)
    ghost = SimpleNamespace(
        id=uuid.uuid4(), filename="ghost.txt", storage_path="uploads/missing.bin"
    )

    copied = workspace.copy_attachments_to_inputs(path, [ghost])  # type: ignore[arg-type]

    assert copied == []


# ---------------------------------------------------------------------------
# 디렉토리 → SkillDraftFile 어댑터
# ---------------------------------------------------------------------------


async def test_load_draft_files_roles_and_exclusions(tmp_path: Path) -> None:
    session_id = uuid.uuid4()
    path = workspace.create_workspace(session_id)
    root = workspace.resolve_workspace_dir(path)
    (root / "references").mkdir()
    (root / "inputs").mkdir()
    (root / "SKILL.md").write_text("# draft\n")
    (root / "references" / "guide.md").write_text("guide\n")
    (root / "inputs" / "sample.csv").write_text("a,b\n")  # 시험 입력 — 제외
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")  # 바이너리 — skip

    files = workspace.load_draft_files(path)

    by_path = {f.path: f for f in files}
    assert set(by_path) == {"SKILL.md", "references/guide.md"}
    assert by_path["SKILL.md"].role == "skill"
    assert by_path["references/guide.md"].role == "reference"


async def test_load_draft_files_missing_dir_returns_empty() -> None:
    assert workspace.load_draft_files(f"skill-drafts/{uuid.uuid4()}") == []


# ---------------------------------------------------------------------------
# GC — 세션 상태 기준
# ---------------------------------------------------------------------------


async def test_gc_preserves_active_and_recent_sessions(db: AsyncSession, tmp_path: Path) -> None:
    # 리텐션(24h)은 지났지만 abandon 지평(14d) 안쪽 + 대화가 살아 있는 세션 —
    # 재개 가능하므로 보존된다 (AD-2, R2 이후 보존은 abandon 지평까지).
    active_old = await _make_session(db, status="active", age_hours=48, with_conversation=True)
    confirming_old = await _make_session(
        db, status="confirming", age_hours=48, with_conversation=True
    )
    completed_recent = await _make_session(db, status="completed", age_hours=1)

    removed = await workspace.gc_stale_draft_workspaces(db, retention_hours=24)

    assert removed == 0
    for session in (active_old, confirming_old, completed_recent):
        assert session.draft_workspace_path is not None
        assert workspace.resolve_workspace_dir(session.draft_workspace_path).is_dir()


async def test_gc_removes_stale_completed_and_abandoned(db: AsyncSession, tmp_path: Path) -> None:
    completed_old = await _make_session(db, status="completed", age_hours=48)
    abandoned_old = await _make_session(db, status="abandoned", age_hours=48)
    active_old = await _make_session(db, status="active", age_hours=48, with_conversation=True)

    removed = await workspace.gc_stale_draft_workspaces(db, retention_hours=24)

    assert removed == 2
    await db.refresh(completed_old)
    await db.refresh(abandoned_old)
    assert completed_old.draft_workspace_path is None
    assert abandoned_old.draft_workspace_path is None
    assert active_old.draft_workspace_path is not None
    remaining = {p.name for p in (tmp_path / "skill-drafts").iterdir()}
    assert remaining == {str(active_old.id)}


async def test_gc_removes_orphan_dirs_without_session_row(db: AsyncSession, tmp_path: Path) -> None:
    import os

    orphan_id = uuid.uuid4()
    orphan = tmp_path / "skill-drafts" / str(orphan_id)
    orphan.mkdir(parents=True)
    old = (datetime.now(UTC) - timedelta(hours=48)).timestamp()
    os.utime(orphan, (old, old))

    fresh_orphan = tmp_path / "skill-drafts" / str(uuid.uuid4())
    fresh_orphan.mkdir(parents=True)

    removed = await workspace.gc_stale_draft_workspaces(db, retention_hours=24)

    assert removed == 1
    assert not orphan.exists()
    assert fresh_orphan.exists()


async def test_gc_rejects_non_positive_retention(db: AsyncSession) -> None:
    with pytest.raises(ValueError, match="retention_hours"):
        await workspace.gc_stale_draft_workspaces(db, retention_hours=0)


async def test_gc_marks_dead_sessions_abandoned(db: AsyncSession, tmp_path: Path) -> None:
    """R2 회귀: 대화가 소실된(conversation_id NULL) 비완료 세션은 리텐션 경과 시
    abandoned로 전이된다 — 전이 경로가 없으면 GC_DELETABLE_STATUSES의 abandoned가
    죽은 규칙이 되어 이탈 세션 워크스페이스가 영구 누수한다."""

    dead = await _make_session(db, status="active", age_hours=48, with_conversation=False)
    fresh = await _make_session(db, status="active", age_hours=1, with_conversation=False)

    await workspace.gc_stale_draft_workspaces(db, retention_hours=24)

    await db.refresh(dead)
    await db.refresh(fresh)
    assert dead.status == "abandoned"
    # 방금 만든(attach 전 창) 세션은 보존.
    assert fresh.status == "active"
    # 전이 시 updated_at이 갱신되므로 이번 패스에선 워크스페이스가 남고,
    # 다음 리텐션 경과 후 회수된다 (2단계 회수).
    assert dead.draft_workspace_path is not None


async def test_gc_marks_long_idle_sessions_abandoned(db: AsyncSession, tmp_path: Path) -> None:
    """R2 회귀: 대화가 살아 있어도 skill_draft_abandon_days(기본 14일) 미활동이면
    abandoned로 전이해 무기한 누수를 막는다."""

    idle = await _make_session(db, status="active", age_hours=15 * 24, with_conversation=True)
    within = await _make_session(db, status="active", age_hours=13 * 24, with_conversation=True)

    await workspace.gc_stale_draft_workspaces(db, retention_hours=24)

    await db.refresh(idle)
    await db.refresh(within)
    assert idle.status == "abandoned"
    assert within.status == "active"


async def test_gc_abandon_days_zero_disables_idle_rule(
    db: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    """R 후속: skill_draft_abandon_days<=0은 idle 규칙 비활성(무기한 보존) —
    max(1)로 강제하면 운영자의 '끄기'(0)가 1일로 둔갑한다."""

    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "skill_draft_abandon_days", 0)
    idle = await _make_session(db, status="active", age_hours=100 * 24, with_conversation=True)

    await workspace.gc_stale_draft_workspaces(db, retention_hours=24)

    await db.refresh(idle)
    assert idle.status == "active"
