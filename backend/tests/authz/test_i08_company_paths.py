"""I-08 company file paths (plan 8.9 "파일 저장 경로"): new files under ``tenants/<id>/``,
reads only when the path's company matches the row; legacy paths = the default company."""

import io
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.migrations.org_authz_v1.contracts import TENANT
from app.models.message_attachment import MessageAttachment
from app.models.skill import Skill
from app.services.artifact_storage import LocalArtifactStorageBackend
from app.services.artifacts.content import _storage_local_path
from app.services.artifacts.errors import ArtifactNotFoundError
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation
from app.storage.paths import (
    DEFAULT_TENANT_ID,
    ForeignTenantPathError,
    check_company_path,
    resolve_company_data_path,
    tenant_relative,
)
from tests.authz.unit_scope_fixture import (
    TEST_ORG_ID,
    TEST_TENANT_ID,
    create_test_text_skill,
    unit_context,
)
from tests.conftest import TEST_USER_ID

OTHER_TENANT = uuid.uuid5(uuid.NAMESPACE_URL, "moldy/i08-other-company")
SKILL_MD = "---\nname: notes\ndescription: Notes skill\n---\n\nBody\n"


def test_default_company_matches_the_m82_backfill() -> None:
    assert DEFAULT_TENANT_ID == TENANT


def test_new_paths_go_to_the_company_folder() -> None:
    assert tenant_relative(TEST_TENANT_ID, "x/y") == f"tenants/{TEST_TENANT_ID}/x/y"
    assert tenant_relative(None, "x/y") == "x/y"


def test_company_prefix_must_match_the_row(tmp_path: Path) -> None:
    own = tmp_path / "tenants" / str(TEST_TENANT_ID) / "f"
    foreign = tmp_path / "tenants" / str(OTHER_TENANT) / "f"
    assert check_company_path(own, tmp_path, TEST_TENANT_ID) == own
    for path, tenant in (
        (foreign, TEST_TENANT_ID),
        (own, None),
        (tmp_path / "tenants" / "not-a-uuid" / "f", TEST_TENANT_ID),
    ):
        with pytest.raises(ForeignTenantPathError):
            check_company_path(path, tmp_path, tenant)


def test_legacy_paths_belong_to_the_default_company(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "skills" / "abc"
    assert check_company_path(legacy, tmp_path, TEST_TENANT_ID) == legacy  # single tenant
    monkeypatch.setattr(settings, "deployment_mode", "multi_tenant")
    assert check_company_path(legacy, tmp_path, DEFAULT_TENANT_ID) == legacy
    assert check_company_path(legacy, tmp_path, None) == legacy  # platform rows
    with pytest.raises(ForeignTenantPathError):
        check_company_path(legacy, tmp_path, OTHER_TENANT)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


async def _attachment(db: AsyncSession, storage_path: Path) -> uuid.UUID:
    _write(storage_path, "secret of another company")
    row = MessageAttachment(
        user_id=TEST_USER_ID,
        org_id=TEST_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        filename="a.txt",
        mime_type="text/plain",
        size_bytes=25,
        storage_path=str(storage_path),
        url="/api/uploads/x",
    )
    db.add(row)
    await db.commit()
    return row.id


async def test_uploads_are_written_to_the_company_folder(
    client: AsyncClient, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    files = {"file": ("hello.txt", io.BytesIO(b"hi"), "text/plain")}
    body = (await client.post("/api/uploads", files=files)).json()
    stored = tmp_path / "uploads" / "tenants" / str(TEST_TENANT_ID) / f"{body['id']}.txt"
    assert stored.read_bytes() == b"hi"
    assert (await client.get(f"/api/uploads/{body['id']}")).status_code == 200


async def test_an_upload_row_naming_another_company_is_404(
    client: AsyncClient, db: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    foreign = tmp_path / "uploads" / "tenants" / str(OTHER_TENANT) / "a.txt"
    upload_id = await _attachment(db, foreign)
    for suffix in ("", "/content"):
        response = await client.get(f"/api/uploads/{upload_id}{suffix}")
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "FILE_NOT_FOUND",
        )


async def test_artifacts_are_stored_and_read_per_company(tmp_path: Path) -> None:
    source = tmp_path / "out.md"
    _write(source, "hello")
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    stored = await storage.put_file(
        conversation_id=uuid.uuid4(),
        artifact_id=uuid.uuid4(),
        version_number=1,
        display_name="out.md",
        source_path=source,
        tenant_id=TEST_TENANT_ID,
    )
    assert stored.object_key.startswith(f"tenants/{TEST_TENANT_ID}/conversations/")
    key = stored.object_key
    assert await storage.local_path(object_key=key, tenant_id=TEST_TENANT_ID) == stored.path
    with pytest.raises(ForeignTenantPathError):
        await storage.local_path(object_key=key, tenant_id=OTHER_TENANT)
    with pytest.raises(ArtifactNotFoundError):  # the routes' 404
        await _storage_local_path(storage, key, OTHER_TENANT)


async def test_skills_are_created_in_the_company_folder_and_guarded_on_read(
    db: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    creation = TextSkillCreation(name="Notes", slug=None, description="d", content=SKILL_MD)
    skill = await create_test_text_skill(db, creation, unit_context(TEST_USER_ID))
    own = f"tenants/{TEST_TENANT_ID}/skills/{skill.id}/SKILL.md"
    assert skill.storage_path == own
    assert resolve_company_data_path(own, TEST_TENANT_ID).is_file()
    assert b"Body" in skill_service.get_file_bytes(skill, "SKILL.md")

    foreign = Skill(
        kind="text",
        tenant_id=TEST_TENANT_ID,
        storage_path=own.replace(str(TEST_TENANT_ID), str(OTHER_TENANT)),
    )
    _write(tmp_path / str(foreign.storage_path), SKILL_MD)
    with pytest.raises(FileNotFoundError):
        skill_service.get_file_bytes(foreign, "SKILL.md")
    assert skill_service.get_skill_files(foreign) == []
    assert await skill_service.read_text_content(foreign) == ""
