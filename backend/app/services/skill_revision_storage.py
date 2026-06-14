from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.config import settings
from app.models.skill import Skill
from app.skills import service as skill_service


@dataclass(frozen=True, slots=True)
class SkillRevisionSnapshot:
    storage_provider: str
    object_key: str
    path: Path
    size_bytes: int
    file_count: int


async def write_skill_revision_snapshot(
    skill: Skill,
    *,
    revision_number: int,
) -> SkillRevisionSnapshot:
    files = await _snapshot_files(skill)
    object_key, path = _revision_path(skill, revision_number)
    await anyio.to_thread.run_sync(_write_zip, path, files)
    size_bytes = await anyio.to_thread.run_sync(_file_size, path)
    return SkillRevisionSnapshot(
        storage_provider="local",
        object_key=object_key,
        path=path,
        size_bytes=size_bytes,
        file_count=len(files),
    )


async def delete_skill_revision_snapshot(object_key: str) -> None:
    path = _object_path(object_key)
    await anyio.to_thread.run_sync(_unlink_missing_ok, path)


async def _snapshot_files(skill: Skill) -> list[tuple[str, bytes]]:
    if skill.kind == "text":
        content = await skill_service.read_text_content(skill)
        return [("SKILL.md", content.encode("utf-8"))]
    files: list[tuple[str, bytes]] = []
    for file_info in skill_service.get_skill_files(skill):
        if file_info.is_dir:
            continue
        files.append((file_info.path, skill_service.get_file_bytes(skill, file_info.path)))
    return files


def _write_zip(path: Path, files: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel_path, content in sorted(files):
            archive.writestr(rel_path, content)


def _file_size(path: Path) -> int:
    return path.stat().st_size


def _unlink_missing_ok(path: Path) -> None:
    path.unlink(missing_ok=True)


def _revision_path(skill: Skill, revision_number: int) -> tuple[str, Path]:
    object_key = f"skill-revisions/{skill.id}/r{revision_number}/skill.zip"
    return object_key, _object_path(object_key)


def _object_path(object_key: str) -> Path:
    path = (Path(settings.data_root) / object_key).resolve()
    root = Path(settings.data_root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("skill revision path escapes data root")
    return path
