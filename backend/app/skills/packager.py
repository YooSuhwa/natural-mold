"""``.skill`` package extraction with zip-slip and symlink defenses.

A package is a ZIP archive containing a ``SKILL.md`` at the root or one
directory level deep. The packager:

1. Validates archive and extracted size against ``settings.skill_max_package_bytes``.
2. Iterates every entry, rejecting symlinks, absolute paths, or any name
   that resolves outside the destination directory.
3. Strips the optional top-level prefix so files always land directly under
   ``<dest>/SKILL.md`` and friends.
4. Computes a SHA-256 of the canonical ``SKILL.md`` body for change tracking.
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.skills.inspector import SkillMetadataError, parse_skill_md


@dataclass
class PackageInfo:
    """Result of a successful package extraction."""

    name: str
    description: str
    body: str
    metadata: dict[str, Any]
    version: str | None
    files: list[str] = field(default_factory=list)
    total_bytes: int = 0
    has_scripts: bool = False
    content_hash: str = ""


class PackageError(ValueError):
    """Raised when a package fails validation."""


def _find_skill_md(zf: zipfile.ZipFile) -> str | None:
    """Locate ``SKILL.md`` at root or one subdir deep."""

    for name in zf.namelist():
        parts = Path(name).parts
        if not parts:
            continue
        if parts[-1] == "SKILL.md" and len(parts) <= 2:
            return name
    return None


def _is_symlink(member: zipfile.ZipInfo) -> bool:
    """Detect Unix symlink entries (mode bit 0o120000)."""

    return (member.external_attr >> 16) & 0o170000 == 0o120000


def _validate_member(member: zipfile.ZipInfo, dest: Path) -> None:
    """Raise ``PackageError`` if the entry is unsafe."""

    if _is_symlink(member):
        raise PackageError(f"symlink not allowed: {member.filename!r}")
    if os.path.isabs(member.filename):
        raise PackageError(f"absolute path not allowed: {member.filename!r}")
    if "\x00" in member.filename:
        raise PackageError(f"null byte in entry name: {member.filename!r}")
    target = (dest / member.filename).resolve()
    try:
        target.relative_to(dest.resolve())
    except ValueError as exc:
        raise PackageError(f"path traversal detected: {member.filename!r}") from exc


def extract_package(zip_bytes: bytes, target_dir: Path) -> PackageInfo:
    """Validate ``zip_bytes`` and extract under ``target_dir``.

    ``target_dir`` is created if missing. Any defense rejection raises
    :class:`PackageError`. On success, returns a :class:`PackageInfo` with the
    parsed frontmatter and the canonical body hash.
    """

    if len(zip_bytes) > settings.skill_max_package_bytes:
        raise PackageError(
            f"package too large: {len(zip_bytes)} bytes (max {settings.skill_max_package_bytes})"
        )

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise PackageError("invalid ZIP file") from exc

    target_dir = Path(target_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    with zf:
        skill_md = _find_skill_md(zf)
        if not skill_md:
            raise PackageError("SKILL.md not found in archive (root or 1-level subdir)")

        raw = zf.read(skill_md)
        try:
            parsed = parse_skill_md(raw, require_metadata=True)
        except SkillMetadataError as exc:
            raise PackageError(str(exc)) from exc
        metadata = parsed["metadata"]
        body = parsed["body"]

        prefix = str(Path(skill_md).parent)
        if prefix == ".":
            prefix = ""

        files: list[str] = []
        total_bytes = 0
        has_scripts = False

        for member in zf.infolist():
            if member.is_dir():
                continue
            _validate_member(member, target_dir)

            rel = member.filename
            if prefix:
                if not rel.startswith(prefix):
                    # File outside the skill subdir — skip silently (siblings).
                    continue
                rel = rel[len(prefix) :].lstrip("/\\")
            if not rel:
                continue

            _raise_if_package_too_large(total_bytes + member.file_size, "after extraction")
            target = (target_dir / rel).resolve()
            try:
                target.relative_to(target_dir)
            except ValueError as exc:
                raise PackageError(f"path traversal detected: {rel!r}") from exc

            target.parent.mkdir(parents=True, exist_ok=True)
            data = zf.read(member.filename)
            _raise_if_package_too_large(total_bytes + len(data), "after extraction")
            target.write_bytes(data)
            files.append(rel.replace("\\", "/"))
            total_bytes += len(data)

            if rel.startswith("scripts/") and rel.endswith(".py"):
                has_scripts = True

    name = (
        metadata.get("name") or (Path(skill_md).parent.name if prefix else "untitled") or "untitled"
    )
    description = str(metadata.get("description") or "")
    version = metadata.get("version")
    if version is not None:
        version = str(version)

    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    return PackageInfo(
        name=str(name),
        description=description,
        body=body,
        metadata=metadata,
        version=version,
        files=sorted(files),
        total_bytes=total_bytes,
        has_scripts=has_scripts,
        content_hash=content_hash,
    )


def _raise_if_package_too_large(size_bytes: int, stage: str) -> None:
    if size_bytes > settings.skill_max_package_bytes:
        raise PackageError(
            f"package too large {stage}: {size_bytes} bytes "
            f"(max {settings.skill_max_package_bytes})"
        )


__all__ = ["PackageError", "PackageInfo", "extract_package"]
