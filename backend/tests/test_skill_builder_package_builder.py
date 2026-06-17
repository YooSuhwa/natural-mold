from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.schemas.skill_builder import SkillDraftFile
from app.skills.package_builder import build_skill_zip_bytes, normalize_draft_path
from app.skills.packager import extract_package


def _skill_md(name: str = "demo") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "demo skill"\n'
        'version: "1.0.0"\n'
        "---\n\n"
        "# Demo\n"
    )


def _draft_file(path: str, content: str) -> SkillDraftFile:
    return SkillDraftFile(path=path, content=content)


def _zip_names(zip_bytes: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return set(zf.namelist())


def test_build_skill_zip_bytes_imports_through_existing_packager(tmp_path: Path) -> None:
    zip_bytes = build_skill_zip_bytes(
        slug="Demo Skill",
        files=[
            _draft_file("SKILL.md", _skill_md()),
            _draft_file("scripts/run.py", "print('ok')\n"),
            _draft_file("references/guide.md", "Use carefully.\n"),
        ],
    )

    info = extract_package(zip_bytes, tmp_path / "skill")

    assert info.name == "demo"
    assert info.files == ["SKILL.md", "references/guide.md", "scripts/run.py"]


def test_build_skill_zip_bytes_requires_skill_md() -> None:
    with pytest.raises(ValueError, match="SKILL.md"):
        build_skill_zip_bytes(
            slug="demo",
            files=[_draft_file("references/guide.md", "hello")],
        )


def test_normalize_draft_path_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="invalid draft file path"):
        normalize_draft_path("../secret.txt")


def test_build_skill_zip_bytes_excludes_evals_by_default() -> None:
    zip_bytes = build_skill_zip_bytes(
        slug="demo",
        files=[
            _draft_file("SKILL.md", _skill_md()),
            _draft_file("evals/evals.json", "{}"),
        ],
    )

    assert _zip_names(zip_bytes) == {"demo/SKILL.md"}


def test_build_skill_zip_bytes_can_include_evals() -> None:
    zip_bytes = build_skill_zip_bytes(
        slug="demo",
        files=[
            _draft_file("SKILL.md", _skill_md()),
            _draft_file("evals/evals.json", "{}"),
        ],
        include_evals=True,
    )

    assert _zip_names(zip_bytes) == {"demo/SKILL.md", "demo/evals/evals.json"}


def test_build_skill_zip_bytes_normalizes_paths_and_slug() -> None:
    zip_bytes = build_skill_zip_bytes(
        slug="Demo Skill!",
        files=[
            _draft_file("/SKILL.md", _skill_md()),
            _draft_file("references\\guide.md", "hello"),
        ],
    )

    assert _zip_names(zip_bytes) == {
        "demo-skill/SKILL.md",
        "demo-skill/references/guide.md",
    }
