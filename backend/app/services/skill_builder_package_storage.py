from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.models.skill_builder_session import JsonValue
from app.skills.package_hash import compute_package_tree_hash
from app.skills.packager import PackageInfo, extract_package
from app.storage.paths import ensure_relative, resolve_data_path


@dataclass(frozen=True, slots=True)
class PackageReplacement:
    info: PackageInfo
    content_hash: str
    storage_path: str


def replace_skill_storage(
    *,
    skill_id: uuid.UUID,
    current_kind: str,
    current_storage_path: str | None,
    zip_bytes: bytes,
) -> PackageReplacement:
    storage_path = ensure_relative(f"skills/{skill_id}")
    root = _skill_root(skill_id, current_kind, current_storage_path)
    with TemporaryDirectory() as temp_dir:
        extracted = Path(temp_dir) / "skill"
        info = extract_package(zip_bytes, extracted)
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(extracted, root)
    return PackageReplacement(
        info=info,
        content_hash=compute_package_tree_hash(root),
        storage_path=storage_path,
    )


def package_metadata(info: PackageInfo, name: str) -> dict[str, JsonValue]:
    return {
        "name": name,
        "version": info.version,
        "files": info.files,
        "has_scripts": info.has_scripts,
        "frontmatter": info.metadata,
    }


def _skill_root(
    skill_id: uuid.UUID,
    current_kind: str,
    current_storage_path: str | None,
) -> Path:
    if current_storage_path is None:
        return resolve_data_path(f"skills/{skill_id}")
    current_path = resolve_data_path(current_storage_path)
    if current_kind == "text":
        return current_path.parent
    return current_path
