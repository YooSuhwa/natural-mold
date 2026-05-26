"""Skills domain tests — text + package CRUD, content hash, file serving."""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.skills import service as skill_service
from app.skills.packager import PackageError, extract_package
from app.storage.paths import resolve_data_path
from tests.conftest import TEST_USER_ID

OTHER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zip_with(files: dict[str, str | bytes], prefix: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            path = f"{prefix}/{name}" if prefix else name
            data = content.encode("utf-8") if isinstance(content, str) else content
            zf.writestr(path, data)
    return buf.getvalue()


def _skill_md(name: str = "demo", description: str = "demo skill") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{description}"\n'
        "version: \"1.0.0\"\n"
        "---\n\n"
        "# Demo body\n"
        "Use scripts/run.py.\n"
    )


# ===========================================================================
# Service — text skills
# ===========================================================================


class TestTextSkill:
    @pytest.mark.asyncio
    async def test_create_persists_to_disk(
        self, db: AsyncSession, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            skill = await skill_service.create_text_skill(
                db,
                user_id=TEST_USER_ID,
                name="My Skill",
                slug=None,
                description="desc",
                content="hello world",
            )
            await db.commit()
            await db.refresh(skill)

            assert skill.kind == "text"
            assert skill.slug == "my-skill"
            assert skill.size_bytes == len(b"hello world")
            assert skill.content_hash and len(skill.content_hash) == 64
            assert skill.storage_path
            assert resolve_data_path(skill.storage_path).read_text() == "hello world"  # noqa: ASYNC240

    @pytest.mark.asyncio
    async def test_update_text_content_rehashes(
        self, db: AsyncSession, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            skill = await skill_service.create_text_skill(
                db,
                user_id=TEST_USER_ID,
                name="A",
                slug="a",
                description=None,
                content="v1",
            )
            await db.commit()
            old_hash = skill.content_hash
            await skill_service.update_text_content(db, skill=skill, content="v2 longer")
            await db.commit()
            await db.refresh(skill)

            assert skill.content_hash != old_hash
            assert skill.size_bytes == len(b"v2 longer")
            assert skill.storage_path is not None
            assert resolve_data_path(skill.storage_path).read_text() == "v2 longer"  # noqa: ASYNC240

    @pytest.mark.asyncio
    async def test_delete_removes_disk(
        self, db: AsyncSession, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            skill = await skill_service.create_text_skill(
                db,
                user_id=TEST_USER_ID,
                name="ToDelete",
                slug="to-delete",
                description=None,
                content="bye",
            )
            await db.commit()
            assert skill.storage_path is not None
            skill_dir = resolve_data_path(skill.storage_path).parent
            assert skill_dir.is_dir()
            await skill_service.delete_skill(db, skill)
            await db.commit()
            assert not skill_dir.exists()

    @pytest.mark.asyncio
    async def test_list_filters(self, db: AsyncSession, tmp_path: Path) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            await skill_service.create_text_skill(
                db,
                user_id=TEST_USER_ID,
                name="Alpha",
                slug="alpha",
                description=None,
                content="a",
            )
            await skill_service.create_text_skill(
                db,
                user_id=TEST_USER_ID,
                name="Bravo",
                slug="bravo",
                description=None,
                content="b",
            )
            await db.commit()

        all_ = await skill_service.list_skills(db, TEST_USER_ID)
        assert len(all_) == 2

        only_alpha = await skill_service.list_skills(db, TEST_USER_ID, query="alph")
        assert len(only_alpha) == 1
        assert only_alpha[0].name == "Alpha"


# ===========================================================================
# Packager — zip slip + symlink defenses
# ===========================================================================


class TestPackager:
    def test_extract_basic(self, tmp_path: Path) -> None:
        zb = _zip_with(
            {
                "SKILL.md": _skill_md("pkg", "package"),
                "scripts/run.py": "print(1)",
            }
        )
        info = extract_package(zb, tmp_path)
        assert info.name == "pkg"
        assert info.has_scripts is True
        assert "scripts/run.py" in info.files
        assert (tmp_path / "SKILL.md").is_file()
        assert info.content_hash
        assert info.version == "1.0.0"

    def test_extract_with_prefix(self, tmp_path: Path) -> None:
        zb = _zip_with(
            {
                "SKILL.md": _skill_md("nested"),
                "scripts/main.py": "pass",
            },
            prefix="nested-skill",
        )
        info = extract_package(zb, tmp_path)
        assert info.name == "nested"
        assert (tmp_path / "SKILL.md").is_file()
        assert (tmp_path / "scripts" / "main.py").is_file()

    def test_zip_slip_rejected(self, tmp_path: Path) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", _skill_md())
            zf.writestr("../../../etc/passwd", "evil")
        with pytest.raises(PackageError, match="path traversal"):
            extract_package(buf.getvalue(), tmp_path)

    def test_symlink_rejected(self, tmp_path: Path) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("SKILL.md", _skill_md())
            info = zipfile.ZipInfo("link")
            info.external_attr = (0o120777 & 0xFFFF) << 16
            zf.writestr(info, "/etc/passwd")
        with pytest.raises(PackageError, match="symlink"):
            extract_package(buf.getvalue(), tmp_path)

    def test_missing_skill_md(self, tmp_path: Path) -> None:
        zb = _zip_with({"readme.txt": "hello"})
        with pytest.raises(PackageError, match="SKILL.md"):
            extract_package(zb, tmp_path)

    def test_invalid_zip(self, tmp_path: Path) -> None:
        with pytest.raises(PackageError, match="invalid ZIP"):
            extract_package(b"not a zip", tmp_path)


# ===========================================================================
# Service — package skills
# ===========================================================================


class TestPackageSkill:
    @pytest.mark.asyncio
    async def test_create_package(self, db: AsyncSession, tmp_path: Path) -> None:
        zb = _zip_with(
            {
                "SKILL.md": _skill_md("pkg-svc"),
                "scripts/main.py": "print(2)",
            }
        )
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            skill = await skill_service.create_package_skill(
                db, user_id=TEST_USER_ID, zip_bytes=zb
            )
            await db.commit()
            await db.refresh(skill)

            assert skill.kind == "package"
            assert skill.package_metadata
            assert skill.package_metadata["has_scripts"] is True
            files = skill_service.get_skill_files(skill)
            paths = {f.path for f in files}
            assert "SKILL.md" in paths
            assert "scripts/main.py" in paths

    @pytest.mark.asyncio
    async def test_get_file_bytes_traversal_rejected(
        self, db: AsyncSession, tmp_path: Path
    ) -> None:
        zb = _zip_with({"SKILL.md": _skill_md("trav")})
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            skill = await skill_service.create_package_skill(
                db, user_id=TEST_USER_ID, zip_bytes=zb
            )
            await db.commit()
            await db.refresh(skill)

            with pytest.raises(ValueError, match="escapes skill root"):
                skill_service.get_file_bytes(skill, "../etc/passwd")


# ===========================================================================
# Router — text + package CRUD + file serving
# ===========================================================================


class TestRouter:
    @pytest.mark.asyncio
    async def test_create_and_get_text(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            resp = await client.post(
                "/api/skills",
                json={"name": "Router Skill", "content": "hi"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["kind"] == "text"
            assert data["slug"] == "router-skill"
            sid = data["id"]

            resp = await client.get(f"/api/skills/{sid}")
            assert resp.status_code == 200
            assert resp.json()["name"] == "Router Skill"

    @pytest.mark.asyncio
    async def test_list_filter_kind(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            await client.post(
                "/api/skills", json={"name": "T1", "content": "x"}
            )
            zb = _zip_with({"SKILL.md": _skill_md("p1")})
            await client.post(
                "/api/skills/upload",
                files={"file": ("p.skill", zb, "application/zip")},
            )

            resp = await client.get("/api/skills", params={"kind": "package"})
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["kind"] == "package"

    @pytest.mark.asyncio
    async def test_patch_metadata(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            resp = await client.post(
                "/api/skills", json={"name": "Old", "content": "x"}
            )
            sid = resp.json()["id"]

            resp = await client.patch(
                f"/api/skills/{sid}",
                json={"name": "New", "description": "updated"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "New"
            assert data["description"] == "updated"

    @pytest.mark.asyncio
    async def test_put_text_content(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            resp = await client.post(
                "/api/skills", json={"name": "C", "content": "v1"}
            )
            sid = resp.json()["id"]
            old_hash = resp.json()["content_hash"]

            resp = await client.put(
                f"/api/skills/{sid}/content", json={"content": "v2 changed"}
            )
            assert resp.status_code == 200
            assert resp.json()["content_hash"] != old_hash

            resp = await client.get(f"/api/skills/{sid}/content")
            assert resp.status_code == 200
            assert resp.json()["content"] == "v2 changed"

    @pytest.mark.asyncio
    async def test_upload_package_router(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        zb = _zip_with(
            {"SKILL.md": _skill_md("router-pkg"), "scripts/run.py": "pass"}
        )
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            resp = await client.post(
                "/api/skills/upload",
                files={"file": ("p.skill", zb, "application/zip")},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["kind"] == "package"
            sid = data["id"]

            resp = await client.get(f"/api/skills/{sid}/files")
            assert resp.status_code == 200
            paths = {f["path"] for f in resp.json()}
            assert "SKILL.md" in paths
            assert "scripts/run.py" in paths

            resp = await client.get(f"/api/skills/{sid}/files/scripts/run.py")
            assert resp.status_code == 200
            assert resp.text == "pass"

    @pytest.mark.asyncio
    async def test_file_traversal_rejected(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        zb = _zip_with({"SKILL.md": _skill_md()})
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            resp = await client.post(
                "/api/skills/upload",
                files={"file": ("p.skill", zb, "application/zip")},
            )
            sid = resp.json()["id"]

            resp = await client.get(
                f"/api/skills/{sid}/files/../../../etc/passwd"
            )
            assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_delete_router(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        with patch.object(skill_service.settings, "data_root", str(tmp_path)):
            resp = await client.post(
                "/api/skills", json={"name": "Bye", "content": "x"}
            )
            sid = resp.json()["id"]

            resp = await client.delete(f"/api/skills/{sid}")
            assert resp.status_code == 204

            resp = await client.get(f"/api/skills/{sid}")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_package_returns_422(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/skills/upload",
            files={"file": ("bad", b"not a zip", "application/zip")},
        )
        assert resp.status_code == 422


# ===========================================================================
# Isolation — list returns only own skills
# ===========================================================================


class TestIsolation:
    @pytest.mark.asyncio
    async def test_list_other_user_excluded(
        self, db: AsyncSession, tmp_path: Path
    ) -> None:
        # Seed a skill for OTHER_USER_ID directly (no FK enforcement on aiosqlite).
        other = Skill(
            id=uuid.uuid4(),
            user_id=OTHER_USER_ID,
            name="other",
            slug="other",
            description=None,
            kind="text",
            storage_path=None,
            content_hash=None,
            size_bytes=0,
            version=None,
            package_metadata=None,
            used_by_count=0,
        )
        db.add(other)
        await db.commit()

        result = await skill_service.list_skills(db, TEST_USER_ID)
        assert all(s.user_id == TEST_USER_ID for s in result)
