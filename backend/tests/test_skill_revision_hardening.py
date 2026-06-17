from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import skill_revision_service
from app.services.skill_revision_storage import write_skill_revision_snapshot
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content(name: str = "revision-hardening") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when testing revision hardening."\n'
        "---\n\n"
        "Use when testing revision hardening.\n"
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _zip_names(zip_path: Path) -> set[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return set(archive.namelist())


def _moldy_metadata(
    *,
    key: str,
    definition_key: str,
    env_name: str,
    timeout_seconds: int,
    requires_network: bool,
) -> str:
    network = "true" if requires_network else "false"
    return (
        "credential_requirements:\n"
        f"  - key: {key}\n"
        f"    definition_key: {definition_key}\n"
        "    required: true\n"
        f"    label: {key.title()}\n"
        "    fields:\n"
        "      - api_key\n"
        "    env_map:\n"
        f"      api_key: {env_name}\n"
        "execution_profile:\n"
        f"  requires_network: {network}\n"
        f"  timeout_seconds: {timeout_seconds}\n"
    )


@pytest.mark.asyncio
async def test_revision_snapshot_excludes_non_hashable_package_files(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content(),
                    "scripts/run.py": "print('ok')\n",
                    "references/a/b/c/d/e/f/g/guide.md": "deep reference",
                    ".DS_Store": "desktop metadata",
                    ".moldy-output/result.txt": "generated output",
                }
            ),
        )

        snapshot = await write_skill_revision_snapshot(skill, revision_number=1)

    assert _zip_names(snapshot.path) == {
        "SKILL.md",
        "references/a/b/c/d/e/f/g/guide.md",
        "scripts/run.py",
    }
    assert snapshot.file_count == 3


@pytest.mark.asyncio
async def test_package_rollback_rehydrates_moldy_credentials_and_execution_profile(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    initial_metadata = _moldy_metadata(
        key="openai",
        definition_key="openai",
        env_name="OPENAI_API_KEY",
        timeout_seconds=12,
        requires_network=True,
    )
    changed_metadata = _moldy_metadata(
        key="naver",
        definition_key="naver_search",
        env_name="NAVER_CLIENT_SECRET",
        timeout_seconds=30,
        requires_network=False,
    )
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content(),
                    "agents/moldy.yaml": initial_metadata,
                }
            ),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path="agents/moldy.yaml",
            content=changed_metadata.encode("utf-8"),
        )
        skill.credential_requirements = [
            {
                "key": "naver",
                "definition_key": "naver_search",
                "required": True,
                "label": "Naver",
                "fields": ["api_key"],
                "env_map": {"api_key": "NAVER_CLIENT_SECRET"},
            }
        ]
        skill.execution_profile = {"requires_network": False, "timeout_seconds": 30}
        await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_file_update",
        )

        await skill_revision_service.rollback_to_revision(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            revision=first,
        )

    assert skill.credential_requirements == [
        {
            "key": "openai",
            "definition_key": "openai",
            "required": True,
            "label": "Openai",
            "fields": ["api_key"],
            "env_map": {"api_key": "OPENAI_API_KEY"},
        }
    ]
    assert skill.execution_profile == {"requires_network": True, "timeout_seconds": 12}
