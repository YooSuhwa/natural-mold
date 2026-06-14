from __future__ import annotations

from app.models.skill import Skill
from app.skills.inspector import SkillMetadataError, list_files, parse_skill_md
from app.skills.package_hash import compute_package_tree_hash
from app.storage.paths import resolve_data_path


def refresh_package_metadata(skill: Skill) -> None:
    if skill.kind != "package" or not skill.storage_path:
        return
    root = resolve_data_path(skill.storage_path)
    files = list_files(root)
    skill.size_bytes = sum(file.size for file in files if not file.is_dir)
    skill.content_hash = compute_package_tree_hash(root)
    metadata = dict(skill.package_metadata or {})
    metadata["files"] = [file.path for file in files if not file.is_dir]
    skill.package_metadata = metadata


def sync_frontmatter(skill: Skill, body: bytes) -> None:
    try:
        parsed = parse_skill_md(body)
    except SkillMetadataError:
        return
    metadata = parsed.get("metadata") or {}
    if not isinstance(metadata, dict):
        return
    if isinstance(metadata.get("description"), str):
        skill.description = metadata["description"]
    if isinstance(metadata.get("version"), str):
        skill.version = metadata["version"]
    if isinstance(metadata.get("name"), str) and not skill.name.strip():
        skill.name = metadata["name"]
    package_metadata = dict(skill.package_metadata or {})
    package_metadata["frontmatter"] = metadata
    skill.package_metadata = package_metadata
