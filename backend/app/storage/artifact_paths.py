"""Validation of relative paths shared by artifact producers and storage."""

from pathlib import Path

_SKIP_NAMES = {".DS_Store"}
_SKIP_PARTS = {"__pycache__", ".previews"}


class ArtifactPathError(ValueError):
    pass


def validate_artifact_relative_path(logical_path: str) -> None:
    relative = Path(logical_path)
    if relative.is_absolute():
        raise ArtifactPathError("artifact path must be relative")
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArtifactPathError("artifact path contains invalid segments")
    if any(part in _SKIP_PARTS for part in parts) or relative.name in _SKIP_NAMES:
        raise ArtifactPathError("artifact path is excluded")
    if len(parts) > 12:
        raise ArtifactPathError("artifact path is too deep")
    if any(len(part) > 120 for part in parts):
        raise ArtifactPathError("artifact path segment is too long")

    if len(logical_path) > 500:
        raise ArtifactPathError("artifact path is too long")
    if any(ord(ch) < 32 for ch in logical_path):
        raise ArtifactPathError("artifact path contains control characters")
