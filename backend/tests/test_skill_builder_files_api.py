"""드래프트 파일 조회 API (M7 — 레일 소스 뷰).

목록/내용/소유권 404/traversal 차단/inputs·바이너리 제외 + brief의
credential_requirement_count 요약.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill_builder_session import SkillBuilderSession
from app.services import skill_draft_workspace as workspace
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"


@pytest.fixture(autouse=True)
def _tmp_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))


_SKILL_MD = (
    "---\n"
    "name: notes\n"
    'description: "Use when summarizing meeting notes into action items."\n'
    "---\n\n"
    "Use when summarizing meeting notes.\n"
)

_MOLDY_YAML = "credential_requirements:\n  - key: naver_search\n    kind: api_key\n"


async def _make_session(
    db: AsyncSession, *, user_id: uuid.UUID = TEST_USER_ID
) -> SkillBuilderSession:
    session = SkillBuilderSession(user_id=user_id, user_request="테스트", status="active")
    db.add(session)
    await db.flush()
    path = workspace.create_workspace(session.id)
    root = workspace.resolve_workspace_dir(path)
    (root / "references").mkdir()
    (root / "inputs").mkdir()
    (root / "agents").mkdir()
    (root / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (root / "references" / "guide.md").write_text("guide body\n", encoding="utf-8")
    (root / "agents" / "moldy.yaml").write_text(_MOLDY_YAML, encoding="utf-8")
    (root / "inputs" / "sample.csv").write_text("a,b\n", encoding="utf-8")  # 제외 대상
    (root / "logo.png").write_bytes(b"\x89PNG\x00binary")  # 바이너리 skip
    session.draft_workspace_path = path
    await db.commit()
    return session


async def test_list_files_excludes_inputs_and_binaries(
    client: AsyncClient, db: AsyncSession
) -> None:
    session = await _make_session(db)

    response = await client.get(f"{BASE}/{session.id}/files")

    assert response.status_code == 200
    files = response.json()["files"]
    by_path = {f["path"]: f for f in files}
    assert set(by_path) == {"SKILL.md", "references/guide.md", "agents/moldy.yaml"}
    assert by_path["SKILL.md"]["role"] == "skill"
    assert by_path["references/guide.md"]["role"] == "reference"
    assert by_path["SKILL.md"]["size"] > 0


async def test_file_content_returns_exact_match(client: AsyncClient, db: AsyncSession) -> None:
    session = await _make_session(db)

    response = await client.get(
        f"{BASE}/{session.id}/files/content", params={"path": "references/guide.md"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "references/guide.md"
    assert body["content"] == "guide body\n"
    assert body["role"] == "reference"


async def test_file_content_blocks_traversal_and_excluded_paths(
    client: AsyncClient, db: AsyncSession
) -> None:
    session = await _make_session(db)

    for bad in ("../../../etc/passwd", "inputs/sample.csv", "logo.png", "missing.md"):
        response = await client.get(f"{BASE}/{session.id}/files/content", params={"path": bad})
        assert response.status_code == 404, bad


async def test_files_are_owner_scoped(client: AsyncClient, db: AsyncSession) -> None:
    other = await _make_session(db, user_id=uuid.uuid4())

    listing = await client.get(f"{BASE}/{other.id}/files")
    content = await client.get(f"{BASE}/{other.id}/files/content", params={"path": "SKILL.md"})

    assert listing.status_code == 404
    assert content.status_code == 404


async def test_brief_includes_credential_requirement_count(db: AsyncSession) -> None:
    session = await _make_session(db)

    brief = workspace.build_skill_draft_brief(session)

    assert brief["credential_requirement_count"] == 1
