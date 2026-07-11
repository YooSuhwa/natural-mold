"""리비전 스냅샷 파일 API (Phase 2 — 버전 diff/소스 보기).

zip은 디스크 추출 없이 읽고, 내용 조회는 열거 경로 **정확 일치**만 —
traversal/바이너리/2MB 초과/pruned는 전부 404(fail-closed) 계약을 검증한다.
"""

from __future__ import annotations

import uuid
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill
from app.models.skill_revision import SkillRevision
from app.services import skill_revision_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")

_BODY = "Use when summarizing meeting notes."


def _skill_content() -> str:
    return (
        "---\n"
        "name: notes\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        f"{_BODY}\n"
    )


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))


async def _make_skill_with_revision(db: AsyncSession) -> tuple[Skill, SkillRevision]:
    skill = await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Notes",
        slug="notes",
        description="Use when summarizing notes.",
        content=_skill_content(),
    )
    revision = await skill_revision_service.create_revision_for_skill(
        db,
        skill=skill,
        user_id=TEST_USER_ID,
        operation="create",
        changelog_summary="Initial version",
    )
    await db.commit()
    return skill, revision


def _rewrite_snapshot(revision: SkillRevision, entries: dict[str, bytes]) -> None:
    """스냅샷 zip을 임의 내용으로 교체 — 바이너리/상한 케이스 조립용."""

    path = Path(settings.data_root) / revision.object_key
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(name, content)


async def test_files_lists_snapshot_entries(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    skill, revision = await _make_skill_with_revision(db)

    response = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["snapshot_pruned"] is False
    assert [entry["path"] for entry in body["files"]] == ["SKILL.md"]
    assert body["files"][0]["is_binary"] is False
    assert body["files"][0]["size"] > 0


async def test_file_content_exact_match_only(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    skill, revision = await _make_skill_with_revision(db)
    base = f"/api/skills/{skill.id}/revisions/{revision.id}/files/content"

    ok = await client.get(base, params={"path": "SKILL.md"})
    assert ok.status_code == 200, ok.text
    assert _BODY in ok.json()["content"]
    assert ok.json()["path"] == "SKILL.md"

    for bad_path in ("../SKILL.md", "missing.md", "SKILL.md/"):
        missing = await client.get(base, params={"path": bad_path})
        assert missing.status_code == 404, bad_path


async def test_binary_entry_marked_and_content_blocked(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(
        revision,
        {
            "SKILL.md": _skill_content().encode("utf-8"),
            "assets/logo.png": b"\x89PNG\x00\x00binary-bytes",
        },
    )

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    by_path = {entry["path"]: entry for entry in files.json()["files"]}
    assert by_path["assets/logo.png"]["is_binary"] is True
    assert by_path["SKILL.md"]["is_binary"] is False

    blocked = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "assets/logo.png"},
    )
    assert blocked.status_code == 404


async def test_oversize_entry_content_blocked(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(
        revision,
        {"big.md": b"a" * (2 * 1024 * 1024 + 1)},
    )

    blocked = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "big.md"},
    )
    assert blocked.status_code == 404


async def test_missing_snapshot_zip_treated_as_pruned_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """pruned 플래그 없이 zip만 유실된 스냅샷 — 500 대신 pruned 계약 (리뷰 R)."""

    skill, revision = await _make_skill_with_revision(db)
    (Path(settings.data_root) / revision.object_key).unlink()

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    assert files.json() == {"snapshot_pruned": True, "files": []}

    content = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "SKILL.md"},
    )
    assert content.status_code == 404


async def test_pruned_snapshot_explicit_and_content_404(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    skill, revision = await _make_skill_with_revision(db)
    revision.metadata_json = {"snapshot_pruned": True}
    await db.commit()

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    assert files.json() == {"snapshot_pruned": True, "files": []}

    content = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "SKILL.md"},
    )
    assert content.status_code == 404


async def test_foreign_skill_revision_files_404(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """타 유저 스킬은 존재 여부와 무관하게 404 — enumeration-safe."""

    foreign_skill = Skill(
        id=uuid.uuid4(),
        user_id=OTHER_USER_ID,
        name="foreign",
        slug="foreign",
        description=None,
        kind="text",
        storage_path=None,
        content_hash=None,
        size_bytes=0,
        version=None,
        package_metadata=None,
        used_by_count=0,
    )
    db.add(foreign_skill)
    await db.flush()
    foreign_revision = SkillRevision(
        skill_id=foreign_skill.id,
        user_id=OTHER_USER_ID,
        revision_number=1,
        operation="create",
        storage_provider="local",
        object_key=f"skill-revisions/{foreign_skill.id}/r1/skill.zip",
        size_bytes=0,
        file_count=0,
        metadata_json={},
    )
    db.add(foreign_revision)
    await db.commit()

    files = await client.get(
        f"/api/skills/{foreign_skill.id}/revisions/{foreign_revision.id}/files"
    )
    assert files.status_code == 404
    content = await client.get(
        f"/api/skills/{foreign_skill.id}/revisions/{foreign_revision.id}/files/content",
        params={"path": "SKILL.md"},
    )
    assert content.status_code == 404
