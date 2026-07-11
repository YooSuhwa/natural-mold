from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import skill_revision_service
from app.services.skill_revision_storage import write_skill_revision_snapshot
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content(name: str = "revision-demo") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when testing revision snapshots."\n'
        "---\n\n"
        "Use when testing revision snapshots.\n"
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _zip_names(zip_path) -> set[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return set(archive.namelist())


@pytest.mark.asyncio
async def test_write_text_skill_revision_snapshot(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        snapshot = await write_skill_revision_snapshot(skill, revision_number=1)

    assert snapshot.object_key.endswith("/r1/skill.zip")
    assert snapshot.file_count == 1
    assert snapshot.size_bytes > 0
    assert snapshot.path.is_file()
    assert _zip_names(snapshot.path) == {"SKILL.md"}


@pytest.mark.asyncio
async def test_write_package_skill_revision_snapshot(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content("revision-package"),
                    "scripts/run.py": "print('ok')\n",
                }
            ),
        )
        snapshot = await write_skill_revision_snapshot(skill, revision_number=2)

    assert snapshot.object_key.endswith("/r2/skill.zip")
    assert snapshot.file_count == 2
    assert _zip_names(snapshot.path) == {"SKILL.md", "scripts/run.py"}


@pytest.mark.asyncio
async def test_create_revision_for_skill_updates_current_pointer(
    db: AsyncSession,
    tmp_path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        revision = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
            changelog_summary="Initial snapshot",
        )
        await db.commit()

    assert revision.revision_number == 1
    assert revision.content_hash == skill.content_hash
    assert revision.file_count == 1
    assert revision.changelog_summary == "Initial snapshot"
    assert skill.current_revision_id == revision.id


@pytest.mark.asyncio
async def test_list_revisions_returns_newest_first(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        second = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_content_update",
        )

    revisions = await skill_revision_service.list_revisions(db, skill=skill, user_id=TEST_USER_ID)

    assert [revision.id for revision in revisions] == [second.id, first.id]


@pytest.mark.asyncio
async def test_get_revision_is_user_scoped(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        revision = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )

    assert (
        await skill_revision_service.get_revision(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            revision_id=revision.id,
        )
        == revision
    )
    assert (
        await skill_revision_service.get_revision(
            db,
            skill=skill,
            user_id=revision.id,
            revision_id=revision.id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_rollback_to_revision_restores_text_skill_and_creates_revision(
    db: AsyncSession,
    tmp_path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        await skill_service.update_text_content(
            db,
            skill=skill,
            content=_skill_content("revision-updated"),
        )
        second = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_content_update",
        )

        rollback = await skill_revision_service.rollback_to_revision(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            revision=first,
            changelog_summary="Rollback to initial version",
        )
        restored = await skill_service.read_text_content(skill)

    assert second.id != rollback.id
    assert rollback.revision_number == 3
    assert rollback.operation == "rollback"
    assert rollback.restored_from_revision_id == first.id
    assert skill.current_revision_id == rollback.id
    assert "name: revision-demo" in restored


@pytest.mark.asyncio
async def test_rollback_to_revision_restores_package_skill_and_hash(
    db: AsyncSession,
    tmp_path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content("revision-package"),
                    "scripts/run.py": "print('v1')\n",
                }
            ),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        first_hash = skill.content_hash
        await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path="scripts/run.py",
            content=b"print('v2')\n",
        )
        await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_file_update",
        )

        rollback = await skill_revision_service.rollback_to_revision(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            revision=first,
        )
        restored = skill_service.get_file_bytes(skill, "scripts/run.py")

    assert rollback.operation == "rollback"
    assert skill.content_hash == first_hash
    assert restored == b"print('v1')\n"


@pytest.mark.asyncio
async def test_rollback_package_missing_skill_md_fails_before_mutation(
    db: AsyncSession,
    tmp_path,
) -> None:
    """validate-then-mutate (R5) — SKILL.md 없는 스냅샷 rollback은 디스크
    무변경 SnapshotMissing으로 끝난다. rmtree 후에 터지면 부분 변이가 남는다."""

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content("revision-package"),
                    "scripts/run.py": "print('v1')\n",
                }
            ),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        # 스냅샷 zip을 SKILL.md 없는 손상본으로 교체.
        snapshot_path = tmp_path / first.object_key
        with zipfile.ZipFile(snapshot_path, "w") as archive:
            archive.writestr("scripts/run.py", "print('v0')\n")

        with pytest.raises(skill_revision_service.SkillRevisionSnapshotMissing):
            await skill_revision_service.rollback_to_revision(
                db,
                skill=skill,
                user_id=TEST_USER_ID,
                revision=first,
            )
        # 디스크 무변경 — 현재 패키지 파일이 그대로 살아 있어야 한다.
        assert skill_service.get_file_bytes(skill, "SKILL.md")
        assert skill_service.get_file_bytes(skill, "scripts/run.py") == b"print('v1')\n"


@pytest.mark.asyncio
async def test_rollback_package_zip_slip_snapshot_fails_before_mutation(
    db: AsyncSession,
    tmp_path,
) -> None:
    """extract_package가 거부하는 엔트리(traversal 등)도 무변이 SnapshotMissing —
    tempdir 추출은 rmtree 이전이므로 409 계약으로 수렴한다 (R6)."""

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content("revision-package"),
                    "scripts/run.py": "print('v1')\n",
                }
            ),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        snapshot_path = tmp_path / first.object_key
        with zipfile.ZipFile(snapshot_path, "w") as archive:
            archive.writestr("SKILL.md", _skill_content("revision-package"))
            archive.writestr("../evil.py", "print('escape')\n")

        with pytest.raises(skill_revision_service.SkillRevisionSnapshotMissing):
            await skill_revision_service.rollback_to_revision(
                db,
                skill=skill,
                user_id=TEST_USER_ID,
                revision=first,
            )
        # 디스크 무변경.
        assert skill_service.get_file_bytes(skill, "scripts/run.py") == b"print('v1')\n"


@pytest.mark.asyncio
async def test_rollback_package_malformed_yaml_fails_before_mutation(
    db: AsyncSession,
    tmp_path,
) -> None:
    """패키지 스냅샷의 깨진 YAML frontmatter — extract_package 내부 파싱은
    SkillMetadataError만 PackageError로 래핑하므로 validate 선검증 없이는
    500으로 샜다. 무변이 SnapshotMissing으로 수렴 (R7)."""

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content("revision-package"),
                    "scripts/run.py": "print('v1')\n",
                }
            ),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        snapshot_path = tmp_path / first.object_key
        with zipfile.ZipFile(snapshot_path, "w") as archive:
            archive.writestr("SKILL.md", "---\nname: [unclosed\n---\nbody\n")
            archive.writestr("scripts/run.py", "print('v0')\n")

        with pytest.raises(skill_revision_service.SkillRevisionSnapshotMissing):
            await skill_revision_service.rollback_to_revision(
                db,
                skill=skill,
                user_id=TEST_USER_ID,
                revision=first,
            )
        # 디스크 무변경.
        assert skill_service.get_file_bytes(skill, "scripts/run.py") == b"print('v1')\n"


@pytest.mark.asyncio
async def test_rollback_package_bumps_last_modified_at(
    db: AsyncSession,
    tmp_path,
) -> None:
    """패키지 rollback도 형제 변이처럼 last_modified_at을 갱신한다 (R5)."""

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes({"SKILL.md": _skill_content("revision-package")}),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        before = skill.last_modified_at
        await skill_revision_service.rollback_to_revision(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            revision=first,
        )

    assert skill.last_modified_at is not None
    assert before is None or skill.last_modified_at >= before


@pytest.mark.asyncio
async def test_list_revisions_limit_none_is_unbounded(
    db: AsyncSession,
    tmp_path,
) -> None:
    """limit=None 전수 열거 — retention prune이 기본 100 창 밖 리비전을
    영구히 놓치지 않기 위한 계약 (R5)."""

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Limit Demo",
            slug="limit-demo",
            description="Use when testing revision limits.",
            content=_skill_content("limit-demo"),
        )
        for _ in range(3):
            await skill_revision_service.create_revision_for_skill(
                db,
                skill=skill,
                user_id=TEST_USER_ID,
                operation="manual_edit",
            )

    bounded = await skill_revision_service.list_revisions(
        db, skill=skill, user_id=TEST_USER_ID, limit=1
    )
    unbounded = await skill_revision_service.list_revisions(
        db, skill=skill, user_id=TEST_USER_ID, limit=None
    )
    assert len(bounded) == 1
    assert len(unbounded) == 3
