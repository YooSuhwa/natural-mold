"""Frontmatter parsing and safe filesystem helpers for skills.

All filesystem reads enforce a containment check — the resolved target must
live under the skill's storage root. Symlinks that escape the root are
rejected. This is the single trust boundary for serving skill files to the
HTTP API and to the deep-agents runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter

from app.skills.package_hash import is_excluded_package_path


class SkillMetadataError(ValueError):
    """Raised when a SKILL.md document lacks required skill metadata."""


@dataclass(frozen=True)
class FileInfo:
    path: str  # POSIX-style relative path
    size: int
    is_dir: bool


def validate_skill_metadata(metadata: dict[str, Any]) -> None:
    """Validate Deep Agents' required SKILL.md frontmatter fields."""

    for field_name in ("name", "description"):
        value = metadata.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise SkillMetadataError(
                f"SKILL.md frontmatter field {field_name!r} must be a non-empty string"
            )


def parse_skill_md(
    raw: str | bytes,
    *,
    require_metadata: bool = False,
) -> dict[str, Any]:
    """Parse a SKILL.md document into ``{"metadata": {...}, "body": "..."}``.

    ``frontmatter`` accepts both bytes and str; we normalize the return shape
    so callers don't need to know the parser internals.
    """

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    post = frontmatter.loads(raw)
    metadata = dict(post.metadata or {})
    if require_metadata:
        validate_skill_metadata(metadata)
    return {"metadata": metadata, "body": post.content or ""}


def _resolve_safely(root: Path, rel: str) -> Path:
    """Resolve ``rel`` against ``root`` and ensure it stays inside.

    Raises ``ValueError`` for absolute paths or anything that escapes ``root``
    via traversal or symlink.
    """

    if rel.startswith("/") or "\x00" in rel:
        raise ValueError(f"invalid relative path: {rel!r}")
    root_resolved = root.resolve()
    target = (root_resolved / rel).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes skill root: {rel!r}") from exc
    return target


def list_files(root: Path | str, max_depth: int | None = None) -> list[FileInfo]:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return []
    items: list[FileInfo] = []
    for entry in sorted(
        root_path.rglob("*"),
        key=lambda path: path.relative_to(root_path).as_posix(),
    ):
        try:
            rel = entry.relative_to(root_path)
        except ValueError:
            continue
        if is_excluded_package_path(rel) or entry.is_symlink():
            continue
        if max_depth is not None and len(rel.parts) > max_depth:
            continue
        try:
            size = entry.stat().st_size if entry.is_file() else 0
        except OSError:
            size = 0
        items.append(FileInfo(path=rel.as_posix(), size=size, is_dir=entry.is_dir()))
    return items


def read_file_safe(root: Path | str, rel: str, *, max_bytes: int = 5_242_880) -> bytes:
    """Read ``rel`` under ``root`` with traversal protection.

    Raises ``FileNotFoundError`` if the target is missing, ``ValueError`` if
    the path escapes the root, and a generic ``ValueError`` if the file
    exceeds ``max_bytes`` (default 5 MiB).
    """

    target = _resolve_safely(Path(root), rel)
    if not target.is_file():
        raise FileNotFoundError(f"not a file: {rel!r}")
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file too large ({size} bytes > {max_bytes})")
    return target.read_bytes()


__all__ = [
    "FileInfo",
    "SkillMetadataError",
    "list_files",
    "parse_skill_md",
    "read_file_safe",
    "validate_skill_metadata",
]
