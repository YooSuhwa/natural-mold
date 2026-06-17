from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.skills import service as skill_service


def _zip_with(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _zip_names(zip_bytes: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        return set(archive.namelist())


def _skill_md(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "portable export test skill"\n'
        'version: "1.0.0"\n'
        "---\n\n"
        "# Portable export\n"
    )


@pytest.mark.asyncio
async def test_export_package_excludes_evals_by_default(
    client: AsyncClient, tmp_path: Path
) -> None:
    zip_bytes = _zip_with(
        {
            "SKILL.md": _skill_md("router-pkg"),
            "scripts/run.py": "pass",
            "evals/evals.json": '{"evals":[]}',
        }
    )
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        created = await client.post(
            "/api/skills/upload",
            files={"file": ("p.skill", zip_bytes, "application/zip")},
        )
        assert created.status_code == 201
        skill_id = created.json()["id"]

        exported = await client.get(f"/api/skills/{skill_id}/export")

        assert exported.status_code == 200
        assert exported.headers["content-type"] == "application/zip"
        assert 'filename="router-pkg.skill"' in exported.headers["content-disposition"]
        assert _zip_names(exported.content) == {
            "router-pkg/SKILL.md",
            "router-pkg/scripts/run.py",
        }


@pytest.mark.asyncio
async def test_export_package_can_include_evals(client: AsyncClient, tmp_path: Path) -> None:
    zip_bytes = _zip_with(
        {
            "SKILL.md": _skill_md("router-pkg"),
            "scripts/run.py": "pass",
            "evals/evals.json": '{"evals":[]}',
        }
    )
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        created = await client.post(
            "/api/skills/upload",
            files={"file": ("p.skill", zip_bytes, "application/zip")},
        )
        assert created.status_code == 201
        skill_id = created.json()["id"]

        exported = await client.get(f"/api/skills/{skill_id}/export?include_evals=true")

        assert exported.status_code == 200
        assert _zip_names(exported.content) == {
            "router-pkg/SKILL.md",
            "router-pkg/scripts/run.py",
            "router-pkg/evals/evals.json",
        }


@pytest.mark.asyncio
async def test_export_text_skill_rejected(client: AsyncClient, tmp_path: Path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        created = await client.post(
            "/api/skills",
            json={"name": "Text Skill", "content": _skill_md("text-skill")},
        )
        assert created.status_code == 201
        skill_id = created.json()["id"]

        exported = await client.get(f"/api/skills/{skill_id}/export")

        assert exported.status_code == 422
        assert exported.json()["error"]["code"] == "INVALID_SKILL_PACKAGE"
