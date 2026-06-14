from __future__ import annotations

import asyncio
import zipfile
from dataclasses import dataclass
from pathlib import Path

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
    await asyncio.to_thread(_write_zip, path, files)
    size_bytes = await asyncio.to_thread(lambda: path.stat().st_size)
    return SkillRevisionSnapshot(
        storage_provider="local",
        object_key=object_key,
        path=path,
        size_bytes=size_bytes,
        file_count=len(files),
    )


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


def _revision_path(skill: Skill, revision_number: int) -> tuple[str, Path]:
    object_key = f"skill-revisions/{skill.id}/r{revision_number}/skill.zip"
    path = (Path(settings.data_root) / object_key).resolve()
    root = Path(settings.data_root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("skill revision path escapes data root")
    return object_key, path
