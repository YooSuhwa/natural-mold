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


async def test_binary_sniff_boundary_contract(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """8KB sniff 비대칭 계약 고정 — 첫 널바이트가 8KB 뒤인 파일은 목록엔
    텍스트(is_binary=False)로 뜨지만 content는 전량 검사로 404(fail-closed).
    누가 content 검사를 head-sniff로 '일관화'하면 이 테스트가 레드가 된다."""

    skill, revision = await _make_skill_with_revision(db)
    late_null = b"a" * 9000 + b"\x00" + b"b" * 10
    _rewrite_snapshot(revision, {"late-null.md": late_null})

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    entry = files.json()["files"][0]
    assert entry["path"] == "late-null.md"
    assert entry["is_binary"] is False  # head 8KB에는 널바이트 없음

    content = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "late-null.md"},
    )
    assert content.status_code == 404  # 전량 검사는 fail-closed


async def test_exact_display_cap_is_served(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """정확히 2MB인 파일은 200 — 상한 비교가 >에서 >=로 회귀하면 레드."""

    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(revision, {"exact.md": b"a" * (2 * 1024 * 1024)})

    content = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "exact.md"},
    )
    assert content.status_code == 200, content.text
    assert len(content.json()["content"]) == 2 * 1024 * 1024


async def test_rollback_pruned_snapshot_conflict_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """pruned/zip 유실 리비전 rollback은 409 명시 응답 — files/content와 대칭."""

    skill, revision = await _make_skill_with_revision(db)
    revision.metadata_json = {"snapshot_pruned": True}
    await db.commit()

    pruned = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert pruned.status_code == 409, pruned.text
    assert pruned.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"

    revision.metadata_json = {}
    await db.commit()
    (Path(settings.data_root) / revision.object_key).unlink()

    missing = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert missing.status_code == 409, missing.text


async def test_corrupt_snapshot_zip_treated_as_unavailable_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """중단된 쓰기 등으로 손상된 zip — 유실과 동일 계약(500 금지, R5)."""

    skill, revision = await _make_skill_with_revision(db)
    (Path(settings.data_root) / revision.object_key).write_bytes(b"not-a-zip")

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    assert files.json() == {"snapshot_pruned": True, "files": []}

    content = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "SKILL.md"},
    )
    assert content.status_code == 404

    rollback = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert rollback.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"


def _corrupt_member_bytes(path: Path) -> None:
    """central directory는 살리고 첫 멤버의 압축 데이터만 손상 — open은 성공하고
    read(CRC/zlib)에서 터지는 부류를 조립한다 (R6)."""

    data = bytearray(path.read_bytes())
    # local header 30B + filename('SKILL.md'=8B) 이후가 압축 스트림.
    for offset in range(40, 46):
        data[offset] ^= 0xFF
    path.write_bytes(bytes(data))


async def test_member_level_corruption_treated_as_unavailable_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """멤버 바이트 손상(Bad CRC/zlib) — open 검사를 통과해도 유실과 동일 계약 (R6)."""

    skill, revision = await _make_skill_with_revision(db)
    _corrupt_member_bytes(Path(settings.data_root) / revision.object_key)

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    assert files.json() == {"snapshot_pruned": True, "files": []}

    content = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": "SKILL.md"},
    )
    assert content.status_code == 404

    rollback = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert rollback.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"


async def test_snapshot_invalid_frontmatter_rollback_conflict_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """frontmatter 계약 이전의 레거시 SKILL.md 스냅샷 rollback — 형제 케이스
    (유실/손상/SKILL.md 부재)와 같은 409로 수렴, 디스크 무변경 (R6)."""

    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(revision, {"SKILL.md": b"no frontmatter at all"})

    rollback = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert rollback.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"


async def test_snapshot_malformed_yaml_rollback_conflict_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """깨진 YAML frontmatter — frontmatter.loads의 ParserError는 ValueError
    계열이 아니라서 frontmatter 부재(409)와 응답이 갈라졌었다 (R7)."""

    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(revision, {"SKILL.md": b"---\nname: [unclosed\n---\nbody\n"})

    rollback = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert rollback.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"


async def test_snapshot_non_string_yaml_key_rollback_conflict_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """non-string 최상위 YAML 키(`on:`) — frontmatter의 Post(**kw)가 TypeError를
    던져 (ValueError, YAMLError) tuple을 통과하던 클래스. parse_skill_md leaf
    정규화로 형제와 같은 409 (R8)."""

    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(
        revision,
        {"SKILL.md": b"---\non: pushed\nname: x\ndescription: y\n---\nbody\n"},
    )

    rollback = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert rollback.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"


async def test_snapshot_non_utf8_rollback_conflict_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """CRC는 멀쩡한 비 UTF-8 SKILL.md — decode는 zip read 밖에서 터지므로
    별도 처리 없으면 500 (읽기 API는 errors='replace'로 서빙하는 비대칭, R7)."""

    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(revision, {"SKILL.md": b"\xff\xfe not utf-8 bytes"})

    rollback = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert rollback.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"


async def test_overlong_entry_path_excluded_from_files_list(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """content Query 상한(4096)을 넘는 엔트리는 목록에서도 제외 — 목록엔 있는데
    못 여는 파일 비대칭을 상수 공유로 닫는다 (R6)."""

    skill, revision = await _make_skill_with_revision(db)
    overlong = "/".join(["deep"] * 900) + "/leaf.md"  # 4500자+
    assert len(overlong) > 4096
    _rewrite_snapshot(revision, {"SKILL.md": b"ok", overlong: b"unreachable"})

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    assert [entry["path"] for entry in files.json()["files"]] == ["SKILL.md"]


async def test_snapshot_without_skill_md_rollback_conflict_not_500(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """SKILL.md 없는 스냅샷 rollback — 변이 전 검증으로 409 (R5)."""

    skill, revision = await _make_skill_with_revision(db)
    _rewrite_snapshot(revision, {"notes.md": b"no skill md"})

    rollback = await client.post(f"/api/skills/{skill.id}/revisions/{revision.id}/rollback")
    assert rollback.status_code == 409, rollback.text
    assert rollback.json()["error"]["code"] == "SKILL_REVISION_SNAPSHOT_UNAVAILABLE"


async def test_long_entry_path_content_served(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """500자 초과 중첩 경로도 목록과 대칭으로 서빙된다 — 목록엔 있는데
    content는 422로 못 여는 비대칭 방지 (R5)."""

    skill, revision = await _make_skill_with_revision(db)
    long_path = "/".join(["deep"] * 130) + "/leaf.md"  # 650자+
    assert len(long_path) > 500
    _rewrite_snapshot(revision, {long_path: b"deep content"})

    files = await client.get(f"/api/skills/{skill.id}/revisions/{revision.id}/files")
    assert files.status_code == 200, files.text
    assert files.json()["files"][0]["path"] == long_path

    content = await client.get(
        f"/api/skills/{skill.id}/revisions/{revision.id}/files/content",
        params={"path": long_path},
    )
    assert content.status_code == 200, content.text
    assert content.json()["content"] == "deep content"


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
