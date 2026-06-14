from __future__ import annotations

import io
import zipfile

from app.models.skill import Skill
from app.skills.file_service import get_file_bytes, get_skill_files
from app.skills.package_builder import EXCLUDED_EXPORT_DIRS, normalize_draft_path
from app.skills.service import slugify


def build_installed_skill_zip_bytes(skill: Skill, *, include_evals: bool = False) -> bytes:
    if skill.kind != "package":
        raise ValueError("only package skills can be exported")

    folder = slugify(skill.slug or skill.name)
    by_path: dict[str, bytes] = {}
    for file_entry in get_skill_files(skill):
        if file_entry.is_dir:
            continue
        rel_path = normalize_draft_path(file_entry.path)
        top_level = rel_path.split("/", 1)[0]
        if not include_evals and top_level in EXCLUDED_EXPORT_DIRS:
            continue
        by_path[rel_path] = get_file_bytes(skill, rel_path)

    if "SKILL.md" not in by_path:
        raise ValueError("package skill must include SKILL.md")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel_path in sorted(by_path):
            archive.writestr(f"{folder}/{rel_path}", by_path[rel_path])
    return buffer.getvalue()


__all__ = ["build_installed_skill_zip_bytes"]
