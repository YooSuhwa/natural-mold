from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import skill_revision_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


def _skill_md(name: str, body: str = "Use when testing skill revision mutations.") -> str:
    return f'---\nname: {name}\ndescription: "Use when testing revisions."\n---\n\n{body}\n'


def _zip_with(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


async def test_create_text_and_upload_package_create_initial_revisions(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        text_response = await client.post(
            "/api/skills",
            json={"name": "Revision Text", "content": _skill_md("revision-text")},
        )
        package_response = await client.post(
            "/api/skills/upload",
            files={
                "file": (
                    "revision-package.skill",
                    _zip_with(
                        {
                            "SKILL.md": _skill_md("revision-package"),
                            "scripts/run.py": "print('ok')\n",
                        }
                    ),
                    "application/zip",
                )
            },
        )

        text_revisions = await client.get(f"/api/skills/{text_response.json()['id']}/revisions")
        package_revisions = await client.get(
            f"/api/skills/{package_response.json()['id']}/revisions"
        )

    assert text_response.status_code == 201, text_response.text
    assert package_response.status_code == 201, package_response.text
    assert text_response.json()["current_revision_id"] is not None
    assert package_response.json()["current_revision_id"] is not None
    assert [item["operation"] for item in text_revisions.json()] == ["create"]
    assert [item["operation"] for item in package_revisions.json()] == ["create"]
    assert package_revisions.json()[0]["file_count"] == 2


async def test_first_legacy_content_mutation_creates_baseline_then_update_revision(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Legacy Text",
            slug="legacy-text",
            description="Use when testing revisions.",
            content=_skill_md("legacy-text", "Original body."),
        )
        await db.commit()

        response = await client.put(
            f"/api/skills/{skill.id}/content",
            json={"content": _skill_md("legacy-text", "Updated body.")},
        )
        revisions = await client.get(f"/api/skills/{skill.id}/revisions")

    assert response.status_code == 200, response.text
    assert response.json()["current_revision_id"] is not None
    body = revisions.json()
    assert [item["operation"] for item in body] == ["manual_content_update", "create"]

    baseline_detail = await client.get(f"/api/skills/{skill.id}/revisions/{body[1]['id']}")
    assert baseline_detail.json()["metadata_json"]["baseline"] is True
    assert baseline_detail.json()["metadata_json"]["baseline_source"] == "first_mutation"


async def test_metadata_and_package_file_mutations_create_revisions(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        created = await client.post(
            "/api/skills/upload",
            files={
                "file": (
                    "editable.skill",
                    _zip_with(
                        {
                            "SKILL.md": _skill_md("editable"),
                            "scripts/run.py": "print('v1')\n",
                            "notes.md": "temporary\n",
                        }
                    ),
                    "application/zip",
                )
            },
        )
        skill_id = created.json()["id"]

        metadata_response = await client.patch(
            f"/api/skills/{skill_id}",
            json={"description": "Updated metadata"},
        )
        put_response = await client.put(
            f"/api/skills/{skill_id}/files/scripts/run.py",
            json={"content": "print('v2')\n"},
        )
        delete_response = await client.delete(f"/api/skills/{skill_id}/files/notes.md")
        upload_response = await client.post(
            f"/api/skills/{skill_id}/files",
            data={"rel_path": "references/guide.md"},
            files={"file": ("guide.md", b"reference\n", "text/markdown")},
        )
        revisions = await client.get(f"/api/skills/{skill_id}/revisions")

    assert created.status_code == 201, created.text
    assert metadata_response.status_code == 200, metadata_response.text
    assert put_response.status_code == 200, put_response.text
    assert delete_response.status_code == 200, delete_response.text
    assert upload_response.status_code == 201, upload_response.text
    assert [item["operation"] for item in revisions.json()] == [
        "manual_file_update",
        "manual_file_update",
        "manual_file_update",
        "manual_metadata_update",
        "create",
    ]


async def test_builder_improve_on_legacy_skill_creates_pre_mutation_baseline(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    from app.schemas.skill_builder import SkillBuilderMode
    from app.services import skill_builder_service

    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Legacy Builder",
            slug="legacy-builder",
            description="Use when testing revisions.",
            content=_skill_md("legacy-builder", "Original body."),
        )
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="Improve this skill",
            mode=SkillBuilderMode.IMPROVE,
            source_skill_id=skill.id,
        )
        await skill_builder_service.save_draft_package(
            db,
            session,
            draft={
                "name": "Legacy Builder",
                "slug": "legacy-builder",
                "description": "Use when testing revisions.",
                "files": [
                    {
                        "path": "SKILL.md",
                        "content": _skill_md("legacy-builder", "Improved body."),
                        "role": "skill",
                    }
                ],
                "credential_requirements": [],
                "execution_profile": {},
            },
        )

        updated = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
        revisions = await skill_revision_service.list_revisions(
            db,
            skill=updated,
            user_id=TEST_USER_ID,
        )

    assert [revision.operation for revision in revisions] == ["builder_improvement", "create"]
    assert revisions[1].metadata_json["baseline"] is True
    assert revisions[1].metadata_json["baseline_source"] == "first_mutation"
