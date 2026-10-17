from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from app.models.skill_builder_session import JsonValue
from app.skills.package_hash import compute_package_tree_hash
from app.skills.packager import PackageInfo, extract_package
from app.storage.paths import ensure_relative, resolve_company_data_path, skill_storage_dir


@dataclass(frozen=True, slots=True)
class PackageReplacement:
    info: PackageInfo
    content_hash: str
    storage_path: str


def replace_skill_storage(
    *,
    skill_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    current_kind: str,
    current_storage_path: str | None,
    zip_bytes: bytes,
) -> PackageReplacement:
    storage_path = ensure_relative(
        _skill_dir(skill_id, tenant_id, current_kind, current_storage_path)
    )
    root = resolve_company_data_path(storage_path, tenant_id)  # I-08 fail closed
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
    files: list[JsonValue] = list(info.files)
    return {
        "name": name,
        "version": info.version,
        "files": files,
        "has_scripts": info.has_scripts,
        "frontmatter": info.metadata,
    }


def _skill_dir(
    skill_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    current_kind: str,
    current_storage_path: str | None,
) -> str:
    """The skill keeps its current directory (company folder or legacy, I-08)."""
    if current_storage_path is None:
        return skill_storage_dir(skill_id, tenant_id)
    if current_kind == "text":
        return str(PurePosixPath(current_storage_path).parent)
    return current_storage_path
